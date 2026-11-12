from flask import Blueprint, render_template, request, redirect, url_for, Response, flash, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from app import db
from app.models import (
    Cuenta, Transaccion, Cliente, Medico, Servicio, CuentaPorCobrar, CuentaPorPagar,
    Categoria, CentroCosto, PrestacionServicio, User, Venta, DetalleVenta, Paquete,
    PaqueteItem, CuotaVenta, BloqueoPeriodo, HistorialBloqueo
)
from datetime import datetime, time, date
import calendar
import csv
import traceback
from io import StringIO, BytesIO
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.worksheet.datavalidation import DataValidation

bp = Blueprint('main', __name__)

def limpiar_monto(valor):
    """Limpia números con formato es-PY (1.500.000 o 400,50) o estándar float."""
    if valor is None or valor == '':
        return 0.0
    if isinstance(valor, (int, float)):
        return float(valor)
    s = str(valor).strip().replace('₲', '').replace('US$', '').replace('R$', '').replace('$', '').replace(' ', '')
    if not s:
        return 0.0
    if '.' in s and ',' in s:
        if s.rfind(',') > s.rfind('.'):
            s = s.replace('.', '').replace(',', '.')
        else:
            s = s.replace(',', '')
    elif ',' in s:
        s = s.replace(',', '.')
    elif '.' in s:
        partes = s.split('.')
        if len(partes) > 2 or (len(partes) == 2 and len(partes[1]) == 3):
            s = s.replace('.', '')
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0

def sumar_meses(fecha_dt, meses):
    """Suma N meses exactos a una fecha respetando el fin de mes."""
    if meses <= 0:
        return fecha_dt
    mes = fecha_dt.month - 1 + meses
    anio = fecha_dt.year + mes // 12
    mes = mes % 12 + 1
    dia = min(fecha_dt.day, calendar.monthrange(anio, mes)[1])
    if isinstance(fecha_dt, datetime):
        return fecha_dt.replace(year=anio, month=mes, day=dia)
    return date(anio, mes, dia)

def verificar_bloqueo_fecha(fecha_eval, cuenta_id=None):
    """Verifica si una fecha cae en un período bloqueado activo para la cuenta o todas las cuentas."""
    if not fecha_eval:
        return None
    f_date = fecha_eval.date() if isinstance(fecha_eval, datetime) else fecha_eval
    bloqueos = BloqueoPeriodo.query.filter_by(estado='Bloqueado').all()
    for b in bloqueos:
        if b.cuenta_id is None or (cuenta_id and b.cuenta_id == int(cuenta_id)):
            if b.fecha_desde and b.fecha_hasta:
                if b.fecha_desde <= f_date <= b.fecha_hasta:
                    cta_txt = b.cuenta.nombre if b.cuenta else 'Todas las cuentas'
                    return f"Período bloqueado ({b.fecha_desde.strftime('%d/%m/%Y')} - {b.fecha_hasta.strftime('%d/%m/%Y')}, {cta_txt}). No se permiten alteraciones en esta fecha."
    return None

def obtener_o_crear_categoria_plan(nombre_plan):
    """Obtiene o crea automáticamente la categoría de Ingreso con el nombre del plan vendido."""
    if not nombre_plan:
        return None
    nombre_limpio = nombre_plan.strip()
    cat = Categoria.query.filter(Categoria.nombre.ilike(nombre_limpio)).first()
    if not cat:
        cat = Categoria(nombre=nombre_limpio, tipo='Ingreso', activo=True)
        db.session.add(cat)
        db.session.flush()
    return cat.id

def recalcular_estado_cxc_y_venta(cxc):
    """Recalcula Pagado, Saldo, Estado y Cuenta de un título CxC y de su Venta vinculada."""
    if not cxc:
        return
    txs = Transaccion.query.filter_by(cxc_id=cxc.id).all()
    pagado_gs = 0.0
    ultima_cuenta = None
    ultimo_metodo = None
    for t in txs:
        eq = t.equivalente_gs if (t.equivalente_gs and t.equivalente_gs > 0) else (t.monto * (t.cotizacion or 1.0))
        if t.tipo == 'Ingreso' and not t.es_devolucion:
            pagado_gs += eq
            if t.cuenta:
                ultima_cuenta = t.cuenta
            if t.metodo_pago:
                ultimo_metodo = t.metodo_pago
        elif t.tipo == 'Salida' or t.es_devolucion:
            pagado_gs -= eq

    if pagado_gs < 0:
        pagado_gs = 0.0

    cxc.pagado = round(pagado_gs, 2)
    if cxc.estado != 'Cancelado':
        cxc.saldo = max(0.0, round((cxc.monto or 0.0) - cxc.pagado - (cxc.descuento or 0.0), 2))
        if cxc.saldo <= 0.5 and (cxc.pagado > 0 or (cxc.descuento or 0) > 0):
            cxc.saldo = 0.0
            cxc.estado = 'Pagado'
        elif cxc.pagado > 0:
            cxc.estado = 'Parcial'
        else:
            cxc.estado = 'Pendiente'
            cxc.cuenta_id = None
            cxc.cuenta_nombre = '-'
            cxc.metodo_pago = '-'

    if ultima_cuenta and cxc.pagado > 0:
        cxc.cuenta_id = ultima_cuenta.id
        cxc.cuenta_nombre = ultima_cuenta.nombre
    if ultimo_metodo and cxc.pagado > 0:
        cxc.metodo_pago = ultimo_metodo

    if cxc.venta_id:
        venta = Venta.query.get(cxc.venta_id)
        if venta:
            recalcular_venta(venta)

def recalcular_venta(venta):
    """Sincroniza el Total Pagado, Saldo, Estado y Cuenta de una Venta según sus cuotas en CxC."""
    if not venta:
        return
    cuotas_cxc = CuentaPorCobrar.query.filter_by(venta_id=venta.id).all()
    if not cuotas_cxc:
        return

    activas = [c for c in cuotas_cxc if c.estado != 'Cancelado']
    total_pagado = sum((c.pagado or 0.0) for c in cuotas_cxc)
    total_saldo = sum((c.saldo or 0.0) for c in activas)
    cuentas_usadas = list({c.cuenta_nombre for c in cuotas_cxc if c.cuenta_nombre and c.cuenta_nombre != '-' and (c.pagado or 0) > 0})

    venta.pagado = round(total_pagado, 2)
    venta.saldo = round(total_saldo, 2)
    if not cuentas_usadas:
        venta.cuenta_nombre = '-'
    elif len(cuentas_usadas) == 1:
        venta.cuenta_nombre = cuentas_usadas[0]
    else:
        venta.cuenta_nombre = 'Varias'

    if not activas:
        venta.estado = 'Cancelada'
        venta.saldo = 0.0
    elif venta.saldo <= 0.5 and venta.pagado > 0:
        venta.saldo = 0.0
        venta.estado = 'Pagado'
    elif venta.pagado > 0:
        venta.estado = 'Parcial'
    else:
        venta.estado = 'Pendiente'

    for cv in venta.cuotas:
        match = next((c for c in cuotas_cxc if c.numero_cuota_str == cv.numero_cuota), None)
        if match:
            cv.estado = match.estado

def recalcular_estado_cxp(cxp):
    """Recalcula Pagado, Saldo, Estado y Cuenta de un título de Cuentas a Pagar."""
    if not cxp:
        return
    txs = Transaccion.query.filter_by(cxp_id=cxp.id).all()
    pagado_val = 0.0
    ultima_cuenta = None
    ultimo_metodo = None
    for t in txs:
        if cxp.moneda == 'PYG':
            val = t.equivalente_gs if (t.equivalente_gs and t.equivalente_gs > 0) else (t.monto * (t.cotizacion or 1.0))
        else:
            val = t.monto if t.moneda == cxp.moneda else ((t.equivalente_gs or t.monto) / (t.cotizacion or 1.0))

        if t.tipo == 'Salida' and not t.es_devolucion:
            pagado_val += val
            if t.cuenta:
                ultima_cuenta = t.cuenta
            if t.metodo_pago:
                ultimo_metodo = t.metodo_pago
        elif t.tipo == 'Ingreso' or t.es_devolucion:
            pagado_val -= val

    if pagado_val < 0:
        pagado_val = 0.0

    cxp.pagado = round(pagado_val, 2)
    if cxp.estado != 'Cancelado':
        cxp.saldo = max(0.0, round((cxp.monto or 0.0) - cxp.pagado, 2))
        if cxp.saldo <= 0.01 and cxp.pagado > 0:
            cxp.saldo = 0.0
            cxp.estado = 'Pagado'
        elif cxp.pagado > 0:
            cxp.estado = 'Parcial'
        else:
            cxp.estado = 'Pendiente'
            cxp.cuenta_id = None
            cxp.cuenta_nombre = '-'

    if ultima_cuenta and cxp.pagado > 0:
        cxp.cuenta_id = ultima_cuenta.id
        cxp.cuenta_nombre = ultima_cuenta.nombre
    if ultimo_metodo and cxp.pagado > 0:
        cxp.metodo_pago = ultimo_metodo

def _autoajustar_columnas_xlsx(ws):
    for col in ws.columns:
        max_len = max((len(str(cell.value or '')) for cell in col), default=10)
        col_letter = col[0].column_letter
        ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 45)

def _leer_filas_archivo_importacion(archivo):
    """Lee tanto archivos .xlsx (openpyxl) como .csv y devuelve la lista de filas."""
    nombre = (archivo.filename or '').lower()
    raw = archivo.read()
    if nombre.endswith('.xlsx'):
        wb = openpyxl.load_workbook(BytesIO(raw), data_only=True)
        ws = wb['Transacciones'] if 'Transacciones' in wb.sheetnames else wb.active
        filas = []
        for row in ws.iter_rows(values_only=True):
            fila_str = []
            for val in row:
                if isinstance(val, (datetime, date)):
                    fila_str.append(val.strftime('%d/%m/%Y'))
                elif val is None:
                    fila_str.append('')
                else:
                    fila_str.append(str(val).strip())
            filas.append(fila_str)
        return filas
    else:
        contenido = raw.decode('utf-8-sig', errors='ignore')
        lineas = [l for l in contenido.splitlines() if l.strip()]
        if not lineas:
            return []
        delim = ';' if ';' in lineas[0] else ','
        return list(csv.reader(StringIO(contenido), delimiter=delim))

@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user)
            if user.username.lower() == 'recepcion':
                return redirect(url_for('main.ventas_vista'))
            return redirect(url_for('main.pyg'))
        else:
            flash('Usuario o contraseña incorrectos', 'danger')
            
    return render_template('login.html')

@bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('main.login'))

@bp.route('/actualizar-db-urgente')
@login_required
def actualizar_db_urgente():
    if current_user.username.lower() == 'recepcion':
        return "No autorizado", 403
    db.create_all()
    return "¡Esquema de base de datos sincronizado correctamente!"

@bp.route('/limpiar-datos-prueba')
@login_required
def limpiar_datos_prueba():
    """Limpia el cobro erróneo de 22.500.000.000, las pruebas de $100 y TESTE NADOA indicados en el PDF."""
    if current_user.username.lower() == 'recepcion':
        return "No autorizado", 403
    eliminadas = 0
    txs = Transaccion.query.all()
    for t in txs:
        desc_up = (t.descripcion or '').upper()
        if 'TESTE NADOA' in desc_up or (t.monto and t.monto >= 15000000 and t.cuenta and t.cuenta.moneda == 'USD') or ('TRANSFERENCIA' in desc_up and t.monto == 100):
            cxc_rel = CuentaPorCobrar.query.get(t.cxc_id) if t.cxc_id else None
            cxp_rel = CuentaPorPagar.query.get(t.cxp_id) if t.cxp_id else None
            db.session.delete(t)
            db.session.flush()
            if cxc_rel:
                recalcular_estado_cxc_y_venta(cxc_rel)
            if cxp_rel:
                recalcular_estado_cxp(cxp_rel)
            eliminadas += 1
    db.session.commit()
    flash(f'Se limpiaron {eliminadas} registros de prueba/erróneos y se recalcularon los saldos.', 'success')
    return redirect(url_for('main.dashboard'))

@bp.route('/')
@login_required
def pyg():
    if current_user.username.lower() == 'recepcion':
        return redirect(url_for('main.ventas_vista'))

    fecha_desde_str = request.args.get('fecha_desde', '')
    fecha_hasta_str = request.args.get('fecha_hasta', '')

    query_ventas = Venta.query.filter(Venta.estado != 'Cancelada')
    
    if fecha_desde_str:
        dt_desde = datetime.strptime(fecha_desde_str, '%Y-%m-%d')
        query_ventas = query_ventas.filter(Venta.fecha >= dt_desde)
    if fecha_hasta_str:
        dt_hasta = datetime.strptime(fecha_hasta_str, '%Y-%m-%d')
        dt_hasta_fin = datetime.combine(dt_hasta.date(), time.max)
        query_ventas = query_ventas.filter(Venta.fecha <= dt_hasta_fin)

    ventas = query_ventas.all()
    
    total_vendido_bruto = sum((v.total or 0) for v in ventas)
    total_ingresos_netos = sum(((v.total or 0) - (v.iva or 0) - (v.tarifa_pasarela or 0) - (v.descuento or 0)) for v in ventas)
    costo_total_servicios = sum((v.costo_servicios or 0) for v in ventas)
    
    total_egresos_costos = costo_total_servicios
    utilidad_neta = total_ingresos_netos - total_egresos_costos
    
    total_planes = sum(1 for v in ventas if v.paquete_id is not None)
    
    valor_por_profesional = []
    medicos = Medico.query.filter((Medico.activo == True) | (Medico.activo.is_(None))).all()
    
    for m in medicos:
        consultas_totales = 0
        valor_total_medico = 0.0
        esp_lower = (m.especialidad or '').strip().lower()
        nombre_med_lower = (m.nombre or '').strip().lower()
        porc_medico = (m.porcentaje_medico or 70.0) / 100.0

        prestaciones_directas = PrestacionServicio.query.filter_by(medico_id=m.id).all()
        for p in prestaciones_directas:
            consultas_totales += (p.cantidad or 1)
            precio_srv = p.servicio.precio if p.servicio else 0.0
            valor_total_medico += precio_srv * porc_medico

        for v in ventas:
            if v.paquete and v.paquete.items:
                for item in v.paquete.items:
                    nombre_srv = (item.servicio.nombre if item.servicio else '').strip().lower()
                    es_match = False
                    if esp_lower and (esp_lower in nombre_srv or nombre_srv in esp_lower):
                        es_match = True
                    if 'nutricion' in esp_lower and 'nutricionista' in nombre_srv:
                        es_match = True
                    if 'metabol' in esp_lower and 'metabólica' in nombre_srv:
                        es_match = True
                    if 'nutricion' in nombre_med_lower and 'nutricionista' in nombre_srv:
                        es_match = True

                    if es_match:
                        consultas_totales += (item.cantidad or 1)
                        base_calculo = item.costo_total_item if (item.costo_total_item or 0) > 0 else ((item.servicio.precio if item.servicio else 0.0) * (item.cantidad or 1))
                        valor_total_medico += base_calculo * porc_medico

        valor_por_profesional.append({
            'nombre': m.nombre,
            'especialidad': m.especialidad,
            'consultas': consultas_totales,
            'valor': valor_total_medico
        })

    transacciones = Transaccion.query.all()
    return render_template('pyg.html', 
                           transacciones=transacciones, 
                           total_ingresos=total_ingresos_netos, 
                           total_salidas=total_egresos_costos, 
                           utilidad_neta=utilidad_neta,
                           total_planes=total_planes,
                           total_vendido_bruto=total_vendido_bruto,
                           valor_por_profesional=valor_por_profesional,
                           selected_desde=fecha_desde_str,
                           selected_hasta=fecha_hasta_str)

# ==============================================================================
# 1. MÓDULO DE FINANZAS (Páginas 3 a 11)
# ==============================================================================

@bp.route('/finanzas')
@login_required
def dashboard():
    try:
        if current_user.username.lower() == 'recepcion':
            return redirect(url_for('main.ventas_vista'))

        txs_sin_cat = Transaccion.query.filter(Transaccion.categoria_id.is_(None)).all()
        hubo_cambio_cat = False
        for t in txs_sin_cat:
            if t.descripcion and 'Venta #' in t.descripcion:
                try:
                    v_id = int(t.descripcion.split('Venta #')[1].split(')')[0].strip())
                    v_obj = Venta.query.get(v_id)
                    if v_obj:
                        nom_plan = v_obj.plan_nombre_historico or (v_obj.paquete.nombre if v_obj.paquete else None)
                        if nom_plan:
                            t.categoria_id = obtener_o_crear_categoria_plan(nom_plan)
                            t.venta_id = v_obj.id
                            hubo_cambio_cat = True
                except Exception:
                    pass
        if hubo_cambio_cat:
            db.session.commit()

        q = request.args.get('q', '').strip()
        cuenta_id = request.args.get('cuenta_id', type=int)
        categoria_id = request.args.get('categoria_id', type=int)
        centro_id = request.args.get('centro_costo_id', type=int)
        fecha_desde_str = request.args.get('fecha_desde', '')
        fecha_hasta_str = request.args.get('fecha_hasta', '')

        query = Transaccion.query
        if q:
            query = query.filter(Transaccion.descripcion.ilike(f'%{q}%'))
        if cuenta_id:
            query = query.filter_by(cuenta_id=cuenta_id)
        if categoria_id:
            query = query.filter_by(categoria_id=categoria_id)
        if centro_id:
            query = query.filter_by(centro_costo_id=centro_id)
        
        if fecha_desde_str:
            dt_desde = datetime.strptime(fecha_desde_str, '%Y-%m-%d')
            query = query.filter(Transaccion.fecha >= dt_desde)
        if fecha_hasta_str:
            dt_hasta = datetime.strptime(fecha_hasta_str, '%Y-%m-%d')
            dt_hasta_fin = datetime.combine(dt_hasta.date(), time.max)
            query = query.filter(Transaccion.fecha <= dt_hasta_fin)

        transacciones = query.order_by(Transaccion.fecha.desc(), Transaccion.id.desc()).all()

        cuentas = Cuenta.query.filter((Cuenta.activo == True) | (Cuenta.activo.is_(None))).all()
        todas_cuentas = Cuenta.query.all()
        saldos = {'PYG': 0.0, 'USD': 0.0, 'BRL': 0.0}
        
        saldos_acumulados = {}
        for c in todas_cuentas:
            saldos_acumulados[c.id] = c.saldo_inicial if c.saldo_inicial is not None else 0.0

        txs_cronologicas = Transaccion.query.order_by(Transaccion.fecha.asc(), Transaccion.id.asc()).all()
        for t in txs_cronologicas:
            if t.cuenta_id in saldos_acumulados:
                if t.tipo == 'Ingreso':
                    saldos_acumulados[t.cuenta_id] += (t.monto or 0.0)
                else:
                    saldos_acumulados[t.cuenta_id] -= (t.monto or 0.0)
                t.saldo_resultante = saldos_acumulados[t.cuenta_id]
            else:
                t.saldo_resultante = 0.0

        for c in todas_cuentas:
            mon = c.moneda if c.moneda in saldos else 'PYG'
            saldos[mon] += saldos_acumulados.get(c.id, 0.0)

        categorias = Categoria.query.filter((Categoria.activo == True) | (Categoria.activo.is_(None))).all()
        centros_costo = CentroCosto.query.filter((CentroCosto.activo == True) | (CentroCosto.activo.is_(None))).all()
        servicios = Servicio.query.filter((Servicio.activo == True) | (Servicio.activo.is_(None))).all()
        clientes = Cliente.query.filter((Cliente.activo == True) | (Cliente.activo.is_(None))).all()
        bloqueos_activos = BloqueoPeriodo.query.filter_by(estado='Bloqueado').all()

        return render_template('dashboard.html', 
                               transacciones=transacciones, 
                               cuentas=cuentas, 
                               categorias=categorias, 
                               centros_costo=centros_costo,
                               servicios=servicios,
                               clientes=clientes,
                               saldos=saldos,
                               bloqueos_activos=bloqueos_activos,
                               hoy_str=date.today().strftime('%Y-%m-%d'),
                               selected_q=q,
                               selected_cuenta=cuenta_id,
                               selected_categoria=categoria_id,
                               selected_centro=centro_id,
                               selected_desde=fecha_desde_str,
                               selected_hasta=fecha_hasta_str)
    except Exception:
        return f"<div style='padding:25px;font-family:monospace;background:#fff0f0;color:#b00020;border:2px solid #b00020;margin:20px;border-radius:8px;'><h3>Diagnóstico de Error en /finanzas:</h3><pre>{traceback.format_exc()}</pre></div>", 200

@bp.route('/agregar-transaccion', methods=['POST'])
@login_required
def agregar_transaccion():
    cat_id = request.form.get('categoria_id')
    cta_id = request.form.get('cuenta_id', type=int)
    cc_id = request.form.get('centro_costo_id')
    srv_id = request.form.get('servicio_id')
    cliente_id = int(request.form.get('cliente_id')) if request.form.get('cliente_id') else None

    descripcion = request.form.get('descripcion')
    tipo = request.form.get('tipo', 'Ingreso')
    moneda_form = request.form.get('moneda', 'PYG')
    monto_ingresado = limpiar_monto(request.form.get('monto') or 0)
    cotizacion = limpiar_monto(request.form.get('cotizacion') or 1.0)
    if moneda_form == 'PYG' or cotizacion <= 0:
        cotizacion = 1.0

    metodo_pago = request.form.get('metodo_pago', 'Efectivo')
    fecha_str = request.form.get('fecha')
    fecha_transaccion = datetime.strptime(fecha_str, '%Y-%m-%d') if fecha_str else datetime.utcnow()
    observacion = request.form.get('observacion', '')

    if not cta_id:
        flash('Debe seleccionar una cuenta bancaria o caja.', 'danger')
        return redirect(url_for('main.dashboard'))

    cuenta = Cuenta.query.get_or_404(cta_id)
    if moneda_form and cuenta.moneda != moneda_form:
        flash(f'La cuenta "{cuenta.nombre}" es en {cuenta.moneda}, no coincide con la moneda seleccionada ({moneda_form}).', 'danger')
        return redirect(url_for('main.dashboard'))

    msg_bloqueo = verificar_bloqueo_fecha(fecha_transaccion, cuenta.id)
    if msg_bloqueo:
        flash(msg_bloqueo, 'danger')
        return redirect(url_for('main.dashboard'))

    if monto_ingresado > 0:
        equivalente_gs = monto_ingresado if cuenta.moneda == 'PYG' else (monto_ingresado * cotizacion)
        db.session.add(Transaccion(
            descripcion=descripcion,
            tipo=tipo,
            moneda=cuenta.moneda,
            monto=monto_ingresado,
            cotizacion=cotizacion,
            equivalente_gs=equivalente_gs,
            categoria_id=int(cat_id) if cat_id else None,
            cuenta_id=cuenta.id,
            centro_costo_id=int(cc_id) if cc_id else None,
            servicio_id=int(srv_id) if srv_id else None,
            cliente_id=cliente_id,
            metodo_pago=metodo_pago,
            conciliado=False,
            fecha=fecha_transaccion,
            observacion=observacion,
            usuario=current_user.username
        ))
        db.session.commit()
        flash('Transacción registrada con éxito.', 'success')
    return redirect(url_for('main.dashboard'))

@bp.route('/editar-transaccion/<int:id>', methods=['POST'])
@login_required
def editar_transaccion(id):
    t = Transaccion.query.get_or_404(id)
    msg_bloq = verificar_bloqueo_fecha(t.fecha, t.cuenta_id)
    if msg_bloq:
        flash(msg_bloq, 'danger')
        return redirect(url_for('main.dashboard'))

    t.tipo = request.form.get('tipo', t.tipo)
    t.descripcion = request.form.get('descripcion', t.descripcion)
    t.monto = limpiar_monto(request.form.get('monto') or t.monto)
    t.metodo_pago = request.form.get('metodo_pago', t.metodo_pago)
    t.categoria_id = int(request.form.get('categoria_id')) if request.form.get('categoria_id') else None
    t.centro_costo_id = int(request.form.get('centro_costo_id')) if request.form.get('centro_costo_id') else None
    if request.form.get('fecha'):
        nueva_f = datetime.strptime(request.form.get('fecha'), '%Y-%m-%d')
        msg_bloq_nueva = verificar_bloqueo_fecha(nueva_f, t.cuenta_id)
        if msg_bloq_nueva:
            flash(msg_bloq_nueva, 'danger')
            return redirect(url_for('main.dashboard'))
        t.fecha = nueva_f
    t.observacion = request.form.get('observacion', '')
    db.session.commit()
    flash('Transacción actualizada correctamente.', 'success')
    return redirect(url_for('main.dashboard'))

@bp.route('/eliminar-transaccion/<int:id>', methods=['POST'])
@login_required
def eliminar_transaccion(id):
    """Pág. 7: Excluir transacción deshace la baja y reabre el título en CxC o CxP."""
    t = Transaccion.query.get_or_404(id)
    msg_bloqueo = verificar_bloqueo_fecha(t.fecha, t.cuenta_id)
    if msg_bloqueo:
        flash(msg_bloqueo, 'danger')
        return redirect(url_for('main.dashboard'))

    cxc_id = t.cxc_id
    cxp_id = t.cxp_id
    par_id = t.transferencia_par_id

    if not cxc_id and t.descripcion and 'Cuota ' in t.descripcion and '(Venta #' in t.descripcion:
        try:
            concepto_buscado = t.descripcion.replace('Cobro avanzado: ', '').strip()
            cxc_match = CuentaPorCobrar.query.filter_by(concepto=concepto_buscado).first()
            if cxc_match:
                cxc_id = cxc_match.id
        except Exception:
            pass

    if par_id:
        par_tx = Transaccion.query.get(par_id)
        if par_tx:
            db.session.delete(par_tx)

    db.session.delete(t)
    db.session.flush()

    if cxc_id:
        cxc = CuentaPorCobrar.query.get(cxc_id)
        if cxc:
            recalcular_estado_cxc_y_venta(cxc)
            flash(f'Transacción eliminada. El título "{cxc.concepto}" volvió a estado {cxc.estado} y el saldo fue revertido.', 'info')
    elif cxp_id:
        cxp = CuentaPorPagar.query.get(cxp_id)
        if cxp:
            recalcular_estado_cxp(cxp)
            flash(f'Transacción eliminada. La cuenta a pagar "{cxp.concepto}" ({cxp.proveedor}) volvió a estado {cxp.estado}.', 'info')
    else:
        flash('Transacción excluida correctamente y saldo de cuenta revertido.', 'success')

    db.session.commit()
    return redirect(url_for('main.dashboard'))

@bp.route('/transferir-cuentas', methods=['POST'])
@bp.route('/transferir', methods=['POST'])
@login_required
def transferir_cuentas():
    """Pág. 5: Transferencia entre cuentas con cambio de moneda y dos transacciones vinculadas."""
    cuenta_origen_id = request.form.get('cuenta_origen_id', type=int)
    cuenta_destino_id = request.form.get('cuenta_destino_id', type=int)
    monto_salida = limpiar_monto(request.form.get('monto_salida') or request.form.get('monto') or 0)
    cotizacion = limpiar_monto(request.form.get('cotizacion') or 1.0)
    monto_entrada = limpiar_monto(request.form.get('monto_entrada') or 0)
    fecha_str = request.form.get('fecha')
    fecha_tx = datetime.strptime(fecha_str, '%Y-%m-%d') if fecha_str else datetime.utcnow()

    if not cuenta_origen_id or not cuenta_destino_id or cuenta_origen_id == cuenta_destino_id or monto_salida <= 0:
        flash('Verifique las cuentas de origen/destino y que el valor que sale sea mayor a cero.', 'danger')
        return redirect(url_for('main.dashboard'))

    cta_origen = Cuenta.query.get_or_404(cuenta_origen_id)
    cta_destino = Cuenta.query.get_or_404(cuenta_destino_id)

    msg_bloq_orig = verificar_bloqueo_fecha(fecha_tx, cta_origen.id)
    msg_bloq_dest = verificar_bloqueo_fecha(fecha_tx, cta_destino.id)
    if msg_bloq_orig or msg_bloq_dest:
        flash(msg_bloq_orig or msg_bloq_dest, 'danger')
        return redirect(url_for('main.dashboard'))

    if cta_origen.moneda == cta_destino.moneda:
        cotizacion = 1.0
        monto_entrada = monto_salida
    else:
        if cotizacion <= 0 and monto_entrada > 0:
            cotizacion = monto_entrada / monto_salida if cta_destino.moneda == 'PYG' else monto_salida / monto_entrada
        if monto_entrada <= 0:
            if cta_destino.moneda == 'PYG':
                monto_entrada = monto_salida * cotizacion
            elif cta_origen.moneda == 'PYG':
                monto_entrada = monto_salida / cotizacion if cotizacion > 0 else monto_salida
            else:
                monto_entrada = monto_salida * cotizacion

    eq_gs = monto_entrada if cta_destino.moneda == 'PYG' else (monto_salida if cta_origen.moneda == 'PYG' else monto_salida * cotizacion)

    tx_salida = Transaccion(
        descripcion=f"Transferencia saliente a {cta_destino.nombre}",
        tipo='Salida',
        moneda=cta_origen.moneda,
        monto=monto_salida,
        cotizacion=cotizacion,
        equivalente_gs=eq_gs,
        cuenta_id=cta_origen.id,
        metodo_pago='Transferencia',
        conciliado=False,
        fecha=fecha_tx,
        observacion=f"Cambio: {cotizacion} | Equivalente Gs.: {eq_gs:,.0f}",
        usuario=current_user.username
    )
    tx_entrada = Transaccion(
        descripcion=f"Transferencia entrante de {cta_origen.nombre}",
        tipo='Ingreso',
        moneda=cta_destino.moneda,
        monto=monto_entrada,
        cotizacion=cotizacion,
        equivalente_gs=eq_gs,
        cuenta_id=cta_destino.id,
        metodo_pago='Transferencia',
        conciliado=False,
        fecha=fecha_tx,
        observacion=f"Cambio: {cotizacion} | Equivalente Gs.: {eq_gs:,.0f}",
        usuario=current_user.username
    )
    db.session.add(tx_salida)
    db.session.add(tx_entrada)
    db.session.flush()
    tx_salida.transferencia_par_id = tx_entrada.id
    tx_entrada.transferencia_par_id = tx_salida.id
    db.session.commit()
    flash('Transferencia realizada correctamente.', 'success')
    return redirect(url_for('main.dashboard'))

@bp.route('/exportar-excel')
@login_required
def exportar_excel():
    """Pág. 6 y 8: Exporta las transacciones filtradas en .xlsx con Moneda, Monto, Cotización, Equivalente en Gs. y Saldo Cuenta."""
    if current_user.username.lower() == 'recepcion':
        return redirect(url_for('main.ventas_vista'))

    q = request.args.get('q', '').strip()
    cuenta_id = request.args.get('cuenta_id', type=int)
    categoria_id = request.args.get('categoria_id', type=int)
    centro_id = request.args.get('centro_costo_id', type=int)
    fecha_desde_str = request.args.get('fecha_desde', '')
    fecha_hasta_str = request.args.get('fecha_hasta', '')

    saldos_acumulados = {c.id: (c.saldo_inicial or 0.0) for c in Cuenta.query.all()}
    for t_cron in Transaccion.query.order_by(Transaccion.fecha.asc(), Transaccion.id.asc()).all():
        if t_cron.cuenta_id in saldos_acumulados:
            if t_cron.tipo == 'Ingreso':
                saldos_acumulados[t_cron.cuenta_id] += (t_cron.monto or 0.0)
            else:
                saldos_acumulados[t_cron.cuenta_id] -= (t_cron.monto or 0.0)
            t_cron.saldo_resultante = saldos_acumulados[t_cron.cuenta_id]
        else:
            t_cron.saldo_resultante = 0.0

    query = Transaccion.query
    if q:
        query = query.filter(Transaccion.descripcion.ilike(f'%{q}%'))
    if cuenta_id:
        query = query.filter_by(cuenta_id=cuenta_id)
    if categoria_id:
        query = query.filter_by(categoria_id=categoria_id)
    if centro_id:
        query = query.filter_by(centro_costo_id=centro_id)
    if fecha_desde_str:
        query = query.filter(Transaccion.fecha >= datetime.strptime(fecha_desde_str, '%Y-%m-%d'))
    if fecha_hasta_str:
        dt_h = datetime.strptime(fecha_hasta_str, '%Y-%m-%d')
        query = query.filter(Transaccion.fecha <= datetime.combine(dt_h.date(), time.max))

    transacciones = query.order_by(Transaccion.fecha.desc(), Transaccion.id.desc()).all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Transacciones"
    headers = ['Fecha', 'Tipo', 'Descripción', 'Paciente/Cliente', 'Categoría', 'Cuenta', 'Centro de Costo', 'Método', 'Moneda', 'Monto', 'Cotización', 'Equivalente en Gs.', 'Saldo Cuenta']
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")

    for t in transacciones:
        mon = t.cuenta.moneda if t.cuenta else (t.moneda or 'PYG')
        cot = t.cotizacion or 1.0
        eq_gs = t.equivalente_gs if (t.equivalente_gs and t.equivalente_gs > 0) else (t.monto if mon == 'PYG' else t.monto * cot)
        ws.append([
            t.fecha.strftime('%d/%m/%Y') if t.fecha else '',
            t.tipo,
            t.descripcion,
            t.cliente.nombre if t.cliente else 'Particular',
            t.categoria.nombre if t.categoria else '-',
            t.cuenta.nombre if t.cuenta else '-',
            t.centro_costo.nombre if t.centro_costo else '-',
            t.metodo_pago,
            mon,
            round(t.monto or 0, 2),
            round(cot, 2),
            round(eq_gs, 0),
            round(getattr(t, 'saldo_resultante', 0.0), 2)
        ])

    for row in ws.iter_rows(min_row=2, max_col=13):
        mon_fila = row[8].value
        fmt_mon = '#,##0' if mon_fila == 'PYG' else '#,##0.00'
        row[9].number_format = fmt_mon
        row[10].number_format = '#,##0'
        row[11].number_format = '#,##0'
        row[12].number_format = fmt_mon

    _autoajustar_columnas_xlsx(ws)
    out = BytesIO()
    wb.save(out)
    out.seek(0)
    fecha_archivo = date.today().strftime('%Y-%m-%d')
    return Response(out.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment;filename=transacciones_{fecha_archivo}.xlsx"})

@bp.route('/descargar-plantilla')
@login_required
def descargar_plantilla():
    """Pág. 8: Descarga plantilla_transacciones.xlsx con hojas Transacciones, Listas (desplegables) e Instrucciones."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Transacciones"
    headers = ['Fecha', 'Tipo', 'Descripción', 'Paciente/Proveedor', 'Categoría', 'Cuenta', 'Centro de Costo', 'Método', 'Monto', 'Cotización']
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")

    cta_ejemplo = Cuenta.query.first()
    cat_ejemplo = Categoria.query.first()
    cc_ejemplo = CentroCosto.query.first()
    ws.append([
        '04/11/2026', 'Ingreso', 'Consulta particular', 'María Giménez',
        cat_ejemplo.nombre if cat_ejemplo else 'Consultas Médicas',
        cta_ejemplo.nombre if cta_ejemplo else 'Caja Guaranies',
        cc_ejemplo.nombre if cc_ejemplo else 'ADMINISTRATIVO',
        'Efectivo', 350000, 1
    ])

    ws_listas = wb.create_sheet("Listas")
    ws_listas.append(['Cuentas', 'Moneda Cuenta', 'Categorías', 'Centros de Costo', 'Métodos', 'Tipos'])
    for cell in ws_listas[1]:
        cell.font = Font(bold=True)

    cuentas_act = Cuenta.query.filter((Cuenta.activo == True) | (Cuenta.activo.is_(None))).all()
    cats_act = Categoria.query.filter((Categoria.activo == True) | (Categoria.activo.is_(None))).all()
    ccs_act = CentroCosto.query.filter((CentroCosto.activo == True) | (CentroCosto.activo.is_(None))).all()
    metodos = ['Efectivo', 'Transferencia', 'Tarjeta', 'POS', 'PIX']
    tipos = ['Ingreso', 'Salida']

    max_rows = max(len(cuentas_act), len(cats_act), len(ccs_act), len(metodos), len(tipos), 1)
    for i in range(max_rows):
        ws_listas.append([
            cuentas_act[i].nombre if i < len(cuentas_act) else '',
            cuentas_act[i].moneda if i < len(cuentas_act) else '',
            cats_act[i].nombre if i < len(cats_act) else '',
            ccs_act[i].nombre if i < len(ccs_act) else '',
            metodos[i] if i < len(metodos) else '',
            tipos[i] if i < len(tipos) else ''
        ])

    dv_tipo = DataValidation(type="list", formula1='"Ingreso,Salida"', allow_blank=True)
    ws.add_data_validation(dv_tipo)
    dv_tipo.add("B2:B1000")

    dv_metodo = DataValidation(type="list", formula1='"Efectivo,Transferencia,Tarjeta,POS,PIX"', allow_blank=True)
    ws.add_data_validation(dv_metodo)
    dv_metodo.add("H2:H1000")

    if len(cats_act) > 0:
        dv_cat = DataValidation(type="list", formula1=f"Listas!$C$2:$C${len(cats_act)+1}", allow_blank=True)
        ws.add_data_validation(dv_cat)
        dv_cat.add("E2:E1000")
    if len(cuentas_act) > 0:
        dv_cta = DataValidation(type="list", formula1=f"Listas!$A$2:$A${len(cuentas_act)+1}", allow_blank=True)
        ws.add_data_validation(dv_cta)
        dv_cta.add("F2:F1000")
    if len(ccs_act) > 0:
        dv_cc = DataValidation(type="list", formula1=f"Listas!$D$2:$D${len(ccs_act)+1}", allow_blank=True)
        ws.add_data_validation(dv_cc)
        dv_cc.add("G2:G1000")

    ws_inst = wb.create_sheet("Instrucciones")
    ws_inst.append(["Instrucciones para importar transacciones en ClínicaFin"])
    ws_inst[1][0].font = Font(bold=True, size=12)
    ws_inst.append(["1. Complete sus registros en la hoja 'Transacciones' a partir de la fila 2."])
    ws_inst.append(["2. Fecha: formato dd/mm/aaaa dentro de un período abierto."])
    ws_inst.append(["3. Tipo: Ingreso o Salida."])
    ws_inst.append(["4. Cuenta, Categoría y Centro de Costo deben existir en Cadastros (consulte la hoja 'Listas')."])
    ws_inst.append(["5. Monto: siempre en la moneda de la cuenta elegida, mayor a cero."])
    ws_inst.append(["6. Cotización: obligatoria si la cuenta es en USD o BRL (en PYG es 1)."])

    _autoajustar_columnas_xlsx(ws)
    _autoajustar_columnas_xlsx(ws_listas)
    _autoajustar_columnas_xlsx(ws_inst)

    out = BytesIO()
    wb.save(out)
    out.seek(0)
    return Response(out.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment;filename=plantilla_transacciones.xlsx"})

@bp.route('/previsualizar-importacion', methods=['POST'])
@login_required
def previsualizar_importacion():
    archivo = request.files.get('archivo')
    if not archivo or not archivo.filename:
        return jsonify({'ok': False, 'error': 'Seleccione un archivo válido (.xlsx o .csv).'})

    try:
        lector = _leer_filas_archivo_importacion(archivo)
        if not lector or len(lector) < 2:
            return jsonify({'ok': False, 'error': 'El archivo está vacío o sin datos.'})

        encabezados = [str(h).strip().lower() for h in lector[0]]
        if 'fecha' not in encabezados[0] or len(encabezados) < 8:
            return jsonify({'ok': False, 'error': 'Plantilla inválida. Descargue y utilice la plantilla oficial.'})

        filas_res = []
        validas = avisos = errores = 0
        vistas_en_archivo = set()

        for idx, fila in enumerate(lector[1:], start=2):
            if not fila or not any(str(c).strip() for c in fila):
                continue
            while len(fila) < 10:
                fila.append('')

            fecha_str, tipo_str, desc_str, pac_str, cat_str, cta_str, cc_str, met_str, monto_str, cot_str = [str(c).strip() for c in fila[:10]]
            estado_fila = 'OK'
            motivo = 'Válida'

            f_dt = None
            for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
                try:
                    f_dt = datetime.strptime(fecha_str, fmt)
                    break
                except ValueError:
                    pass
            if not f_dt:
                estado_fila = 'Error'
                motivo = 'Fecha inválida (use dd/mm/aaaa)'

            monto_val = limpiar_monto(monto_str)
            if estado_fila != 'Error' and tipo_str not in ('Ingreso', 'Salida'):
                estado_fila = 'Error'
                motivo = 'Tipo debe ser Ingreso o Salida'
            if estado_fila != 'Error' and monto_val <= 0:
                estado_fila = 'Error'
                motivo = 'Monto debe ser mayor a cero'

            cta = Cuenta.query.filter(Cuenta.nombre.ilike(cta_str)).first() if cta_str else None
            if estado_fila != 'Error' and not cta:
                estado_fila = 'Error'
                motivo = f'Cuenta "{cta_str}" no existe'

            if estado_fila != 'Error' and cat_str:
                cat = Categoria.query.filter(Categoria.nombre.ilike(cat_str)).first()
                if not cat:
                    estado_fila = 'Error'
                    motivo = f'Categoría "{cat_str}" no existe'

            if estado_fila != 'Error' and cc_str:
                cc = CentroCosto.query.filter(CentroCosto.nombre.ilike(cc_str)).first()
                if not cc:
                    estado_fila = 'Error'
                    motivo = f'Centro de Costo "{cc_str}" no existe'

            if estado_fila != 'Error' and f_dt and cta:
                if verificar_bloqueo_fecha(f_dt, cta.id):
                    estado_fila = 'Error'
                    motivo = 'Período cerrado o bloqueado'

            if estado_fila != 'Error' and f_dt and cta:
                clave = (f_dt.strftime('%Y-%m-%d'), desc_str.lower(), cta.id, round(monto_val, 2))
                existe_bd = Transaccion.query.filter(
                    db.func.date(Transaccion.fecha) == f_dt.date(),
                    Transaccion.descripcion.ilike(desc_str),
                    Transaccion.cuenta_id == cta.id,
                    Transaccion.monto == monto_val
                ).first()
                if existe_bd or clave in vistas_en_archivo:
                    estado_fila = 'Aviso'
                    motivo = 'Posible duplicado'
                vistas_en_archivo.add(clave)

            if estado_fila == 'Error':
                errores += 1
            elif estado_fila == 'Aviso':
                avisos += 1
                validas += 1
            else:
                validas += 1

            mon_simb = '₲' if (not cta or cta.moneda == 'PYG') else ('US$' if cta.moneda == 'USD' else 'R$')
            filas_res.append({
                'fila': idx,
                'fecha': fecha_str,
                'tipo': tipo_str,
                'descripcion': desc_str,
                'paciente': pac_str,
                'categoria': cat_str,
                'cuenta': cta_str,
                'centro': cc_str,
                'metodo': met_str,
                'monto_fmt': f"{mon_simb} {monto_val:,.0f}".replace(',', '.') if mon_simb == '₲' else f"{mon_simb} {monto_val:,.2f}",
                'monto_raw': monto_str,
                'cotizacion': cot_str or '1',
                'estado': estado_fila,
                'motivo': motivo
            })

        return jsonify({
            'ok': True,
            'total': len(filas_res),
            'validas': validas,
            'avisos': avisos,
            'errores': errores,
            'filas': filas_res
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': f'Error al procesar el archivo: {str(e)}'})

@bp.route('/importar-transacciones', methods=['POST'])
@login_required
def importar_transacciones():
    """Pág. 9: Importa archivos .xlsx o .csv validando cada fila."""
    archivo = request.files.get('archivo')
    if not archivo or not archivo.filename:
        flash('Seleccione un archivo válido para importar.', 'danger')
        return redirect(url_for('main.dashboard'))

    try:
        filas = _leer_filas_archivo_importacion(archivo)
        if len(filas) < 2:
            flash('El archivo está vacío o no contiene filas para importar.', 'warning')
            return redirect(url_for('main.dashboard'))

        importadas = 0
        errores = 0
        for fila in filas[1:]:
            if not fila or len(fila) < 9 or not str(fila[0]).strip():
                continue
            try:
                fecha_str, tipo_str, desc_str, pac_str, cat_str, cta_str, cc_str, met_str, monto_str = [str(c).strip() for c in fila[:9]]
                cot_str = str(fila[9]).strip() if len(fila) > 9 else '1'

                try:
                    f_dt = datetime.strptime(fecha_str, '%d/%m/%Y')
                except ValueError:
                    f_dt = datetime.strptime(fecha_str, '%Y-%m-%d')

                cta = Cuenta.query.filter(Cuenta.nombre.ilike(cta_str)).first()
                if not cta or tipo_str not in ('Ingreso', 'Salida'):
                    errores += 1
                    continue

                if verificar_bloqueo_fecha(f_dt, cta.id):
                    errores += 1
                    continue

                monto_val = limpiar_monto(monto_str)
                cot_val = limpiar_monto(cot_str) or 1.0
                if monto_val <= 0:
                    errores += 1
                    continue

                cat = Categoria.query.filter(Categoria.nombre.ilike(cat_str)).first() if cat_str else None
                cc = CentroCosto.query.filter(CentroCosto.nombre.ilike(cc_str)).first() if cc_str else None
                cli = Cliente.query.filter(Cliente.nombre.ilike(pac_str)).first() if pac_str else None

                eq_gs = monto_val if cta.moneda == 'PYG' else monto_val * cot_val
                db.session.add(Transaccion(
                    fecha=f_dt,
                    tipo=tipo_str,
                    descripcion=desc_str,
                    cliente_id=cli.id if cli else None,
                    categoria_id=cat.id if cat else None,
                    cuenta_id=cta.id,
                    centro_costo_id=cc.id if cc else None,
                    metodo_pago=met_str or 'Efectivo',
                    moneda=cta.moneda,
                    monto=monto_val,
                    cotizacion=1.0 if cta.moneda == 'PYG' else cot_val,
                    equivalente_gs=eq_gs,
                    usuario=current_user.username
                ))
                importadas += 1
            except Exception:
                errores += 1

        db.session.commit()
        if importadas > 0:
            flash(f'{importadas} transacciones importadas correctamente ({errores} filas con error omitidas).', 'success')
        else:
            flash(f'No se importó ninguna fila ({errores} con error).', 'danger')
    except Exception as e:
        flash(f'Plantilla inválida o error al procesar archivo: {str(e)}', 'danger')

    return redirect(url_for('main.dashboard'))

@bp.route('/exportar-pdf')
@login_required
def exportar_pdf():
    return redirect(url_for('main.exportar_excel'))

# ==============================================================================
# BLOQUEOS DE PERÍODO POR RANGO DE FECHAS (Páginas 10 y 11)
# ==============================================================================

@bp.route('/bloqueos')
@login_required
def bloqueos_vista():
    if current_user.username.lower() == 'recepcion':
        return redirect(url_for('main.ventas_vista'))
    bloqueos = BloqueoPeriodo.query.order_by(BloqueoPeriodo.fecha_bloqueo.desc()).all()
    historial = HistorialBloqueo.query.order_by(HistorialBloqueo.fecha_hora.desc()).all()
    cuentas = Cuenta.query.filter((Cuenta.activo == True) | (Cuenta.activo.is_(None))).all()
    return render_template('bloqueos.html', bloqueos=bloqueos, historial=historial, cuentas=cuentas)

@bp.route('/bloquear-periodo', methods=['POST'])
@login_required
def bloquear_periodo():
    desde_str = request.form.get('fecha_desde', '')
    hasta_str = request.form.get('fecha_hasta', '')
    motivo = request.form.get('motivo', 'Cierre estándar').strip() or 'Cierre estándar'
    cuenta_id = request.form.get('cuenta_id')
    cta_val = int(cuenta_id) if cuenta_id and cuenta_id != 'todas' else None

    f_desde = datetime.strptime(desde_str, '%Y-%m-%d').date() if desde_str else date.today()
    f_hasta = datetime.strptime(hasta_str, '%Y-%m-%d').date() if hasta_str else f_desde

    if f_hasta < f_desde:
        flash('La fecha Hasta no puede ser anterior a la fecha Desde.', 'danger')
        return redirect(url_for('main.bloqueos_vista'))

    activos = BloqueoPeriodo.query.filter_by(estado='Bloqueado').all()
    for b in activos:
        misma_cta = (b.cuenta_id == cta_val) or (b.cuenta_id is None) or (cta_val is None)
        if misma_cta and b.fecha_desde and b.fecha_hasta:
            if not (f_hasta < b.fecha_desde or f_desde > b.fecha_hasta):
                flash(f'El rango elegido se superpone con un bloqueo activo ({b.periodo}). Ajuste las fechas.', 'warning')
                return redirect(url_for('main.bloqueos_vista'))

    rango_str = f"{f_desde.strftime('%d/%m/%Y')} - {f_hasta.strftime('%d/%m/%Y')}"
    cta_obj = Cuenta.query.get(cta_val) if cta_val else None
    cta_nombre = cta_obj.nombre if cta_obj else 'Todas las cuentas'

    nuevo_bloqueo = BloqueoPeriodo(
        periodo=rango_str,
        fecha_desde=f_desde,
        fecha_hasta=f_hasta,
        cuenta_id=cta_val,
        usuario=current_user.username if current_user else 'admin',
        estado='Bloqueado',
        motivo=motivo
    )
    db.session.add(nuevo_bloqueo)
    db.session.add(HistorialBloqueo(
        usuario=current_user.username if current_user else 'admin',
        accion='Bloqueo',
        rango=rango_str,
        cuenta_nombre=cta_nombre,
        motivo=motivo
    ))
    db.session.commit()
    flash(f'Período {rango_str} bloqueado correctamente.', 'success')
    return redirect(url_for('main.bloqueos_vista'))

@bp.route('/desbloquear-periodo/<int:id>', methods=['POST'])
@login_required
def desbloquear_periodo(id):
    b = BloqueoPeriodo.query.get_or_404(id)
    motivo = request.form.get('motivo', 'Reapertura autorizada').strip() or 'Reapertura autorizada'
    b.estado = 'Liberado'
    b.motivo_desbloqueo = motivo
    cta_nombre = b.cuenta.nombre if b.cuenta else 'Todas las cuentas'

    db.session.add(HistorialBloqueo(
        usuario=current_user.username if current_user else 'admin',
        accion='Desbloqueo',
        rango=b.periodo,
        cuenta_nombre=cta_nombre,
        motivo=motivo
    ))
    db.session.commit()
    flash('Período desbloqueado con éxito.', 'info')
    return redirect(url_for('main.bloqueos_vista'))

# ==============================================================================
# 2. MÓDULO DE VENTAS (Páginas 12 a 21)
# ==============================================================================

@bp.route('/ventas')
@login_required
def ventas_vista():
    for v_sync in Venta.query.all():
        if not v_sync.cuotas_cxc and v_sync.cliente_id:
            cxc_viejas = CuentaPorCobrar.query.filter(CuentaPorCobrar.concepto.ilike(f'%(Venta #{v_sync.id})%')).all()
            for c_old in cxc_viejas:
                c_old.venta_id = v_sync.id
        recalcular_venta(v_sync)
    db.session.commit()

    q = request.args.get('q', '').strip()
    paquete_id = request.args.get('paquete_id', type=int)
    forma_pago = request.args.get('forma_pago', '').strip()
    cuenta_nombre = request.args.get('cuenta_nombre', '').strip()
    fecha_desde_str = request.args.get('fecha_desde', '')
    fecha_hasta_str = request.args.get('fecha_hasta', '')

    query = Venta.query

    if q:
        query = query.join(Cliente).filter(Cliente.nombre.ilike(f'%{q}%'))
    if paquete_id:
        query = query.filter(Venta.paquete_id == paquete_id)
    if forma_pago:
        query = query.filter(Venta.forma_pago.ilike(f'%{forma_pago}%'))
    if cuenta_nombre:
        query = query.filter(Venta.cuenta_nombre == cuenta_nombre)
    if fecha_desde_str:
        dt_desde = datetime.strptime(fecha_desde_str, '%Y-%m-%d')
        query = query.filter(Venta.fecha >= dt_desde)
    if fecha_hasta_str:
        dt_hasta = datetime.strptime(fecha_hasta_str, '%Y-%m-%d')
        dt_hasta_fin = datetime.combine(dt_hasta.date(), time.max)
        query = query.filter(Venta.fecha <= dt_hasta_fin)

    ventas = query.order_by(Venta.fecha.desc(), Venta.id.desc()).all()
    clientes = Cliente.query.filter((Cliente.activo == True) | (Cliente.activo.is_(None))).all()
    servicios = Servicio.query.filter((Servicio.activo == True) | (Servicio.activo.is_(None))).all()
    paquetes = Paquete.query.filter((Paquete.activo == True) | (Paquete.activo.is_(None))).all()
    cuentas = Cuenta.query.filter((Cuenta.activo == True) | (Cuenta.activo.is_(None))).all()

    return render_template('ventas.html', 
                           ventas=ventas, 
                           clientes=clientes, 
                           servicios=servicios, 
                           paquetes=paquetes, 
                           cuentas=cuentas,
                           selected_q=q,
                           selected_paquete=paquete_id,
                           selected_forma_pago=forma_pago,
                           selected_cuenta_nombre=cuenta_nombre,
                           selected_desde=fecha_desde_str,
                           selected_hasta=fecha_hasta_str)

@bp.route('/exportar-ventas-excel')
@login_required
def exportar_ventas_excel():
    """Pág. 21: Genera ventas_aaaa-mm-dd.xlsx con 3 hojas reales: Ventas (con fila Totales), Cuotas y Cobros."""
    q = request.args.get('q', '').strip()
    paquete_id = request.args.get('paquete_id', type=int)
    forma_pago = request.args.get('forma_pago', '').strip()
    cuenta_nombre = request.args.get('cuenta_nombre', '').strip()
    fecha_desde_str = request.args.get('fecha_desde', '')
    fecha_hasta_str = request.args.get('fecha_hasta', '')

    query = Venta.query
    if q:
        query = query.join(Cliente).filter(Cliente.nombre.ilike(f'%{q}%'))
    if paquete_id:
        query = query.filter(Venta.paquete_id == paquete_id)
    if forma_pago:
        query = query.filter(Venta.forma_pago.ilike(f'%{forma_pago}%'))
    if cuenta_nombre:
        query = query.filter(Venta.cuenta_nombre == cuenta_nombre)
    if fecha_desde_str:
        query = query.filter(Venta.fecha >= datetime.strptime(fecha_desde_str, '%Y-%m-%d'))
    if fecha_hasta_str:
        dt_h = datetime.strptime(fecha_hasta_str, '%Y-%m-%d')
        query = query.filter(Venta.fecha <= datetime.combine(dt_h.date(), time.max))

    ventas = query.order_by(Venta.fecha.desc()).all()
    if not ventas:
        flash('No hay ventas para exportar.', 'warning')
        return redirect(url_for('main.ventas_vista'))

    wb = openpyxl.Workbook()
    ws_ventas = wb.active
    ws_ventas.title = "Ventas"
    headers_v = ['Fecha', 'Paciente', 'Plan', 'Forma de pago', 'Nº cuotas', 'Total (IVA incl.)', 'IVA', '% Tarifa', 'Tarifa de tarjeta', 'Costo de los servicios', 'Margen / Ganancia', 'Cobrado', 'Saldo', 'Estado']
    ws_ventas.append(headers_v)
    for cell in ws_ventas[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")

    sum_total = sum_iva = sum_tarifa = sum_costo = sum_margen = sum_cobrado = sum_saldo = 0
    for v in ventas:
        nombre_cliente = v.cliente.nombre if v.cliente else 'Particular'
        nombre_plan = v.plan_nombre_historico or (v.paquete.nombre if v.paquete else 'Servicios Avulsos')
        porc_tarifa = '3,3%' if (v.tarifa_pasarela or 0) > 0 else '0%'
        tot = round(v.total or 0)
        iva_v = round(v.iva or 0)
        tar_v = round(v.tarifa_pasarela or 0)
        cos_v = round(v.costo_servicios or 0)
        mar_v = round(v.ganancia_plan or 0)
        cob_v = round(v.pagado or 0)
        sal_v = round(v.saldo or 0)

        sum_total += tot
        sum_iva += iva_v
        sum_tarifa += tar_v
        sum_costo += cos_v
        sum_margen += mar_v
        sum_cobrado += cob_v
        sum_saldo += sal_v

        ws_ventas.append([
            v.fecha.strftime('%d/%m/%Y') if v.fecha else '',
            nombre_cliente, nombre_plan, v.forma_pago or 'Cuotas (CxC)', v.num_cuotas or 1,
            tot, iva_v, porc_tarifa, tar_v, cos_v, mar_v, cob_v, sal_v, v.estado
        ])

    ws_ventas.append(['Totales', '', '', '', '', sum_total, sum_iva, '', sum_tarifa, sum_costo, sum_margen, sum_cobrado, sum_saldo, ''])
    ultima_fila = ws_ventas.max_row
    for cell in ws_ventas[ultima_fila]:
        cell.font = Font(bold=True)

    for row in ws_ventas.iter_rows(min_row=2, max_row=ultima_fila):
        for col_idx in (5, 6, 8, 9, 10, 11, 12):
            row[col_idx].number_format = '#,##0'

    ws_cuotas = wb.create_sheet("Cuotas")
    ws_cuotas.append(['Venta', 'Paciente', 'Cuota', 'Vencimiento', 'Valor', 'Pagado', 'Saldo', 'Estado'])
    for cell in ws_cuotas[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")

    for v in ventas:
        for c in v.cuotas_cxc:
            ws_cuotas.append([
                f"Venta #{v.id}",
                v.cliente.nombre if v.cliente else 'Particular',
                c.numero_cuota_str or c.concepto,
                c.fecha_vencimiento.strftime('%d/%m/%Y') if c.fecha_vencimiento else '',
                round(c.monto or 0),
                round(c.pagado or 0),
                round(c.saldo or 0),
                c.estado
            ])
    for row in ws_cuotas.iter_rows(min_row=2):
        for col_idx in (4, 5, 6):
            row[col_idx].number_format = '#,##0'

    ws_cobros = wb.create_sheet("Cobros")
    ws_cobros.append(['Venta', 'Paciente', 'Fecha', 'Moneda', 'Monto original', 'Cotización', 'Equivalente en Gs.', 'Descuento', 'Cuenta'])
    for cell in ws_cobros[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")

    for v in ventas:
        txs_venta = Transaccion.query.filter_by(venta_id=v.id).all()
        for t in txs_venta:
            ws_cobros.append([
                f"Venta #{v.id}",
                v.cliente.nombre if v.cliente else 'Particular',
                t.fecha.strftime('%d/%m/%Y') if t.fecha else '',
                t.moneda or 'PYG',
                round(t.monto or 0, 2),
                round(t.cotizacion or 1, 2),
                round(t.equivalente_gs or t.monto or 0),
                round(t.descuento_aplicado or 0),
                t.cuenta.nombre if t.cuenta else '-'
            ])
    for row in ws_cobros.iter_rows(min_row=2):
        row[4].number_format = '#,##0' if row[3].value == 'PYG' else '#,##0.00'
        row[5].number_format = '#,##0'
        row[6].number_format = '#,##0'
        row[7].number_format = '#,##0'

    _autoajustar_columnas_xlsx(ws_ventas)
    _autoajustar_columnas_xlsx(ws_cuotas)
    _autoajustar_columnas_xlsx(ws_cobros)

    out = BytesIO()
    wb.save(out)
    out.seek(0)
    fecha_hoy = date.today().strftime('%Y-%m-%d')
    return Response(out.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment;filename=ventas_{fecha_hoy}.xlsx"})

@bp.route('/agregar-venta-completa', methods=['POST'])
@login_required
def agregar_venta_completa():
    """Págs. 16, 17 y 18: Registrar Venta sin cuenta bancaria, sin costo fijo por plan y con cuotas mensuales reales en CxC."""
    cliente_id = int(request.form.get('cliente_id')) if request.form.get('cliente_id') else None
    paquete_id = int(request.form.get('paquete_id')) if request.form.get('paquete_id') else None
    forma_pago = request.form.get('forma_pago', 'Cuotas (CxC)')
    num_cuotas = max(1, int(request.form.get('num_cuotas') or 1))
    fecha_venta = datetime.utcnow()

    msg_bloq = verificar_bloqueo_fecha(fecha_venta, None)
    if msg_bloq:
        flash(msg_bloq, 'danger')
        return redirect(url_for('main.ventas_vista'))

    precio_total = 0.0
    costo_servicios = 0.0
    paquete = None
    
    if paquete_id:
        paquete = Paquete.query.get_or_404(paquete_id)
        precio_total += (paquete.precio or 0.0)
        costo_servicios += sum((item.costo_total_item or 0.0) for item in paquete.items)

    servicios_avulsos_ids = request.form.getlist('servicio_avulso_id[]')
    cantidades_avulsas = request.form.getlist('cantidad_avulso[]')
    
    avulsos_seleccionados = []
    for i in range(len(servicios_avulsos_ids)):
        if servicios_avulsos_ids[i]:
            srv_id = int(servicios_avulsos_ids[i])
            cant = int(cantidades_avulsas[i]) if i < len(cantidades_avulsas) and cantidades_avulsas[i] else 1
            srv = Servicio.query.get(srv_id)
            if srv:
                precio_total += (srv.precio or 0.0) * cant
                costo_srv = (srv.costo_unitario or 0.0) * cant * (1 + ((srv.reparto_adicional or 0.0) / 100.0))
                costo_servicios += costo_srv
                avulsos_seleccionados.append({'servicio': srv, 'cantidad': cant})

    if precio_total > 0:
        iva = round(precio_total / 11.0, 2)
        tarifa_pasarela = round(precio_total * 0.033, 2) if ('Crédito' in forma_pago or 'Tarjeta' in forma_pago) else 0.0
        ganancia_plan = round(precio_total - iva - tarifa_pasarela - costo_servicios, 2)
        
        conceptos_desc = []
        if paquete:
            conceptos_desc.append(paquete.nombre)
        if avulsos_seleccionados:
            nombres_avulsos = ", ".join([f"{item['cantidad']}x {item['servicio'].nombre}" for item in avulsos_seleccionados])
            conceptos_desc.append(f"Avulsos: {nombres_avulsos}")
        concepto_venta = " + ".join(conceptos_desc) if conceptos_desc else "Venta General"

        obtener_o_crear_categoria_plan(concepto_venta)

        venta = Venta(
            fecha=fecha_venta,
            cliente_id=cliente_id,
            paquete_id=paquete.id if paquete else None,
            plan_nombre_historico=concepto_venta,
            forma_pago='Cuotas (CxC)' if num_cuotas > 1 else forma_pago,
            num_cuotas=num_cuotas,
            cuenta_nombre='-',
            subtotal=precio_total,
            total=precio_total,
            pagado=0.0,
            saldo=precio_total,
            estado='Pendiente',
            iva=iva,
            tarifa_pasarela=tarifa_pasarela,
            costo_servicios=costo_servicios,
            margen_contribucion=ganancia_plan,
            costo_fijo_plan=0.0,
            ganancia_plan=ganancia_plan
        )
        db.session.add(venta)
        db.session.flush()
        
        for item in avulsos_seleccionados:
            db.session.add(DetalleVenta(
                venta_id=venta.id,
                servicio_id=item['servicio'].id,
                servicio_nombre_historico=item['servicio'].nombre,
                cantidad=item['cantidad'],
                precio_unitario=item['servicio'].precio,
                costo_unitario_historico=item['servicio'].costo_unitario or 0.0,
                reparto_historico=item['servicio'].reparto_adicional or 0.0,
                subtotal=item['servicio'].precio * item['cantidad']
            ))

        cuota_base = int(precio_total // num_cuotas)
        resto = precio_total - (cuota_base * num_cuotas)

        for i in range(1, num_cuotas + 1):
            valor_cuota = cuota_base + (resto if i == num_cuotas else 0)
            fecha_venc = sumar_meses(fecha_venta, i - 1)
            label = "Día de la venta" if i == 1 else f"{i-1} mes(es) después"
            num_str = f"{i} de {num_cuotas}"
            
            db.session.add(CuotaVenta(
                venta_id=venta.id,
                numero_cuota=num_str,
                vencimiento_label=label,
                fecha_vencimiento=fecha_venc,
                valor=valor_cuota,
                estado='Pendiente'
            ))
            
            db.session.add(CuentaPorCobrar(
                cliente_id=cliente_id,
                venta_id=venta.id,
                numero_cuota_str=num_str,
                concepto=f"Cuota {num_str} - {concepto_venta} (Venta #{venta.id})",
                moneda='PYG',
                metodo_pago='-',
                cuenta_nombre='-',
                monto=valor_cuota,
                pagado=0.0,
                descuento=0.0,
                saldo=valor_cuota,
                fecha_emision=fecha_venta,
                fecha_vencimiento=fecha_venc,
                estado='Pendiente'
            ))
            
        db.session.commit()
        flash(f'Venta #{venta.id} registrada exitosamente. Se generaron {num_cuotas} título(s) en Cuentas por Cobrar.', 'success')
        
    return redirect(url_for('main.ventas_vista'))

@bp.route('/cancelar-venta/<int:id>', methods=['POST'])
@login_required
def cancelar_venta(id):
    v = Venta.query.get_or_404(id)
    v.estado = 'Cancelada'
    v.saldo = 0.0
    for cuota in v.cuotas:
        if cuota.estado == 'Pendiente':
            cuota.estado = 'Cancelada'
    for cxc in v.cuotas_cxc:
        if (cxc.pagado or 0) == 0:
            cxc.estado = 'Cancelado'
            cxc.saldo = 0.0
    db.session.commit()
    flash(f'La venta #{v.id} y sus cuotas pendientes han sido canceladas.', 'warning')
    return redirect(url_for('main.ventas_vista'))

@bp.route('/excluir-venta/<int:id>', methods=['POST'])
@login_required
def excluir_venta(id):
    v = Venta.query.get_or_404(id)
    if (v.pagado or 0) > 0:
        flash(f'No se puede excluir la venta #{v.id} porque tiene cobros registrados. Utilice "Cancelar venta" o devuelva los cobros primero.', 'danger')
    else:
        for cxc in v.cuotas_cxc:
            db.session.delete(cxc)
        db.session.delete(v)
        db.session.commit()
        flash(f'La venta #{v.id} ha sido excluida del sistema.', 'success')
    return redirect(url_for('main.ventas_vista'))

# ==============================================================================
# 3. CUENTAS POR COBRAR (Páginas 26 a 33)
# ==============================================================================

@bp.route('/cuentas-por-cobrar')
@login_required
def cxc_vista():
    if current_user.username.lower() == 'recepcion':
        return redirect(url_for('main.ventas_vista'))

    for c_init in CuentaPorCobrar.query.all():
        if not c_init.venta_id and '(Venta #' in (c_init.concepto or ''):
            try:
                c_init.venta_id = int(c_init.concepto.split('(Venta #')[1].split(')')[0])
            except Exception:
                pass
        if (c_init.saldo is None or c_init.saldo == 0) and c_init.estado == 'Pendiente' and (c_init.pagado or 0) == 0:
            c_init.saldo = c_init.monto
    db.session.commit()

    q = request.args.get('q', '').strip()
    paquete_id = request.args.get('paquete_id', type=int)
    metodo_pago = request.args.get('metodo_pago', '').strip()
    cuenta_id = request.args.get('cuenta_id', type=int)
    fecha_desde_str = request.args.get('fecha_desde', '')
    fecha_hasta_str = request.args.get('fecha_hasta', '')

    query = CuentaPorCobrar.query
    if q:
        query = query.join(Cliente).filter(Cliente.nombre.ilike(f'%{q}%'))
    if paquete_id:
        paq_obj = Paquete.query.get(paquete_id)
        if paq_obj:
            query = query.filter(CuentaPorCobrar.concepto.ilike(f'%{paq_obj.nombre}%'))
    if metodo_pago:
        query = query.filter(CuentaPorCobrar.metodo_pago.ilike(f'%{metodo_pago}%'))
    if cuenta_id:
        query = query.filter(CuentaPorCobrar.cuenta_id == cuenta_id)
    if fecha_desde_str:
        dt_d = datetime.strptime(fecha_desde_str, '%Y-%m-%d')
        query = query.filter(CuentaPorCobrar.fecha_vencimiento >= dt_d)
    if fecha_hasta_str:
        dt_h = datetime.strptime(fecha_hasta_str, '%Y-%m-%d')
        query = query.filter(CuentaPorCobrar.fecha_vencimiento <= datetime.combine(dt_h.date(), time.max))

    cxc_list = query.order_by(CuentaPorCobrar.fecha_vencimiento.asc(), CuentaPorCobrar.id.asc()).all()
    clientes = Cliente.query.filter((Cliente.activo == True) | (Cliente.activo.is_(None))).all()
    cuentas = Cuenta.query.filter((Cuenta.activo == True) | (Cuenta.activo.is_(None))).all()
    paquetes = Paquete.query.filter((Paquete.activo == True) | (Paquete.activo.is_(None))).all()
    bloqueos_activos = BloqueoPeriodo.query.filter_by(estado='Bloqueado').all()

    return render_template('cxc.html',
                           cxc_list=cxc_list,
                           clientes=clientes,
                           cuentas=cuentas,
                           paquetes=paquetes,
                           bloqueos_activos=bloqueos_activos,
                           hoy_str=date.today().strftime('%Y-%m-%d'),
                           selected_q=q,
                           selected_paquete=paquete_id,
                           selected_metodo=metodo_pago,
                           selected_cuenta=cuenta_id,
                           selected_desde=fecha_desde_str,
                           selected_hasta=fecha_hasta_str)

@bp.route('/exportar-cxc-excel')
@login_required
def exportar_cxc_excel():
    """Pág. 28: Exportar cuentas_por_cobrar_aaaa-mm-dd.xlsx respetando filtros activos."""
    q = request.args.get('q', '').strip()
    paquete_id = request.args.get('paquete_id', type=int)
    metodo_pago = request.args.get('metodo_pago', '').strip()
    cuenta_id = request.args.get('cuenta_id', type=int)
    fecha_desde_str = request.args.get('fecha_desde', '')
    fecha_hasta_str = request.args.get('fecha_hasta', '')

    query = CuentaPorCobrar.query
    if q:
        query = query.join(Cliente).filter(Cliente.nombre.ilike(f'%{q}%'))
    if paquete_id:
        paq_obj = Paquete.query.get(paquete_id)
        if paq_obj:
            query = query.filter(CuentaPorCobrar.concepto.ilike(f'%{paq_obj.nombre}%'))
    if metodo_pago:
        query = query.filter(CuentaPorCobrar.metodo_pago.ilike(f'%{metodo_pago}%'))
    if cuenta_id:
        query = query.filter(CuentaPorCobrar.cuenta_id == cuenta_id)
    if fecha_desde_str:
        query = query.filter(CuentaPorCobrar.fecha_vencimiento >= datetime.strptime(fecha_desde_str, '%Y-%m-%d'))
    if fecha_hasta_str:
        dt_h = datetime.strptime(fecha_hasta_str, '%Y-%m-%d')
        query = query.filter(CuentaPorCobrar.fecha_vencimiento <= datetime.combine(dt_h.date(), time.max))

    cxc_list = query.order_by(CuentaPorCobrar.fecha_vencimiento.asc(), CuentaPorCobrar.id.asc()).all()
    if not cxc_list:
        flash('No hay registros para exportar.', 'warning')
        return redirect(url_for('main.cxc_vista'))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Cuentas por Cobrar"
    ws.append(['Fecha Emisión', 'Concepto', 'Cliente/Paciente', 'Vencimiento', 'Moneda', 'Total', 'Pagado', 'Saldo', 'Estado', 'Forma de pago', 'Cuenta'])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")

    for x in cxc_list:
        ws.append([
            x.fecha_emision.strftime('%d/%m/%Y') if x.fecha_emision else '',
            x.concepto,
            x.cliente.nombre if x.cliente else 'Particular',
            x.fecha_vencimiento.strftime('%d/%m/%Y') if x.fecha_vencimiento else '',
            x.moneda or 'PYG',
            round(x.monto or 0),
            round(x.pagado or 0),
            round(x.saldo or 0),
            x.estado,
            x.metodo_pago if (x.pagado or 0) > 0 else '-',
            x.cuenta_nombre if (x.pagado or 0) > 0 else '-'
        ])
    for row in ws.iter_rows(min_row=2):
        for col_idx in (5, 6, 7):
            row[col_idx].number_format = '#,##0'

    _autoajustar_columnas_xlsx(ws)
    out = BytesIO()
    wb.save(out)
    out.seek(0)
    fecha_hoy = date.today().strftime('%Y-%m-%d')
    return Response(out.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment;filename=cuentas_por_cobrar_{fecha_hoy}.xlsx"})

@bp.route('/registrar-cobro-avanzado', methods=['POST'])
@login_required
def registrar_cobro_avanzado():
    """Págs. 3, 20, 29, 30 y 33: Cobro multimoneda con validación de saldo, cuenta de misma moneda, fecha y categoría del plan."""
    cxc_id = request.form.get('cxc_id', type=int)
    moneda_cobro = request.form.get('moneda_cobro', 'PYG')
    cotizacion = limpiar_monto(request.form.get('cotizacion') or 1.0)
    monto_recibido = limpiar_monto(request.form.get('monto_recibido') or 0.0)
    descuento = limpiar_monto(request.form.get('descuento') or 0.0)
    motivo_descuento = request.form.get('motivo_descuento', '')
    forma_cobro = request.form.get('forma_cobro', 'Efectivo')
    cuenta_id = request.form.get('cuenta_id', type=int)
    fecha_str = request.form.get('fecha_cobro')
    fecha_cobro = datetime.strptime(fecha_str, '%Y-%m-%d') if fecha_str else datetime.utcnow()

    cxc = CuentaPorCobrar.query.get_or_404(cxc_id)
    if not cuenta_id:
        flash('Debe elegir una Cuenta de Baja válida.', 'danger')
        return redirect(url_for('main.cxc_vista'))

    cuenta = Cuenta.query.get_or_404(cuenta_id)
    if cuenta.moneda != moneda_cobro:
        flash(f'La cuenta elegida ({cuenta.nombre}) es en {cuenta.moneda} y no coincide con la moneda del cobro ({moneda_cobro}).', 'danger')
        return redirect(url_for('main.cxc_vista'))

    msg_bloq = verificar_bloqueo_fecha(fecha_cobro, cuenta.id)
    if msg_bloq:
        flash(msg_bloq, 'danger')
        return redirect(url_for('main.cxc_vista'))

    if moneda_cobro == 'PYG' or cotizacion <= 0:
        cotizacion = 1.0

    saldo_actual_cuota = cxc.saldo if (cxc.saldo is not None and cxc.saldo > 0) else ((cxc.monto or 0.0) - (cxc.pagado or 0.0) - (cxc.descuento or 0.0))
    saldo_neto_exigido = max(0.0, saldo_actual_cuota - descuento)

    if moneda_cobro == 'PYG':
        equivalente_gs = monto_recibido
    else:
        monto_sugerido = round(saldo_neto_exigido / cotizacion, 2)
        if abs(monto_recibido - monto_sugerido) <= 0.02:
            equivalente_gs = saldo_neto_exigido
        else:
            equivalente_gs = round(monto_recibido * cotizacion, 0)

    if equivalente_gs > (saldo_neto_exigido + 10):
        flash(f'El monto ingresado equivale a ₲ {equivalente_gs:,.0f} y supera el saldo de la cuota (₲ {saldo_neto_exigido:,.0f}).', 'danger')
        return redirect(url_for('main.cxc_vista'))

    if equivalente_gs > 0:
        nombre_plan = None
        if cxc.venta:
            nombre_plan = cxc.venta.plan_nombre_historico or (cxc.venta.paquete.nombre if cxc.venta.paquete else None)
        if not nombre_plan and '-' in cxc.concepto and '(Venta #' in cxc.concepto:
            nombre_plan = cxc.concepto.split('-', 1)[1].split('(Venta #')[0].strip()
        cat_plan_id = obtener_o_crear_categoria_plan(nombre_plan or 'Cobro de Cuotas')

        cxc.descuento = (cxc.descuento or 0.0) + descuento

        db.session.add(Transaccion(
            descripcion=f"Cobro avanzado: {cxc.concepto}",
            tipo='Ingreso',
            moneda=cuenta.moneda,
            monto=monto_recibido,
            cotizacion=cotizacion,
            equivalente_gs=equivalente_gs,
            descuento_aplicado=descuento,
            cuenta_id=cuenta.id,
            categoria_id=cat_plan_id,
            cliente_id=cxc.cliente_id,
            cxc_id=cxc.id,
            venta_id=cxc.venta_id,
            metodo_pago=forma_cobro,
            conciliado=False,
            fecha=fecha_cobro,
            observacion=f"Moneda: {moneda_cobro} | Monto: {monto_recibido} | Cotización: {cotizacion} | Equiv. Gs.: {equivalente_gs:,.0f} | Desc.: {descuento} ({motivo_descuento})",
            usuario=current_user.username
        ))
        db.session.flush()
        recalcular_estado_cxc_y_venta(cxc)
        db.session.commit()
        flash('Cobro registrado correctamente. Se actualizaron Cuentas por Cobrar, Ventas y Finanzas.', 'success')
    
    return redirect(url_for('main.cxc_vista'))

@bp.route('/devolver-cobro-cxc/<int:id>', methods=['POST'])
@login_required
def devolver_cobro_cxc(id):
    """Pág. 32: Devolver pago en CxC crea una Salida en Finanzas y reabre el saldo sin borrar el cobro original."""
    cxc = CuentaPorCobrar.query.get_or_404(id)
    tx_origen_id = request.form.get('transaccion_id', type=int)
    monto_devolver = limpiar_monto(request.form.get('monto_devolver') or 0.0)
    cuenta_id = request.form.get('cuenta_id', type=int)
    motivo = request.form.get('motivo', '').strip()
    fecha_str = request.form.get('fecha_devolucion')
    fecha_dev = datetime.strptime(fecha_str, '%Y-%m-%d') if fecha_str else datetime.utcnow()

    if not motivo:
        flash('El motivo de la devolución es obligatorio.', 'danger')
        return redirect(url_for('main.cxc_vista'))

    tx_orig = Transaccion.query.get(tx_origen_id) if tx_origen_id else None
    cuenta = Cuenta.query.get_or_404(cuenta_id or (tx_orig.cuenta_id if tx_orig else cxc.cuenta_id))

    msg_bloq = verificar_bloqueo_fecha(fecha_dev, cuenta.id)
    if msg_bloq:
        flash(msg_bloq, 'danger')
        return redirect(url_for('main.cxc_vista'))

    cotizacion = tx_orig.cotizacion if (tx_orig and tx_orig.cotizacion) else 1.0
    equivalente_gs = monto_devolver if cuenta.moneda == 'PYG' else round(monto_devolver * cotizacion, 0)

    if equivalente_gs <= 0 or equivalente_gs > ((cxc.pagado or 0.0) + 10):
        flash('El monto a devolver no es válido o supera lo pagado en la cuota.', 'danger')
        return redirect(url_for('main.cxc_vista'))

    db.session.add(Transaccion(
        descripcion=f"Devolución de pago: {cxc.concepto} ({motivo})",
        tipo='Salida',
        moneda=cuenta.moneda,
        monto=monto_devolver,
        cotizacion=cotizacion,
        equivalente_gs=equivalente_gs,
        cuenta_id=cuenta.id,
        categoria_id=tx_orig.categoria_id if tx_orig else None,
        cliente_id=cxc.cliente_id,
        cxc_id=cxc.id,
        venta_id=cxc.venta_id,
        metodo_pago=tx_orig.metodo_pago if tx_orig else 'Transferencia',
        fecha=fecha_dev,
        observacion=f"Motivo devolución: {motivo}",
        es_devolucion=True,
        usuario=current_user.username
    ))
    db.session.flush()
    recalcular_estado_cxc_y_venta(cxc)
    db.session.commit()
    flash('Devolución de pago registrada. El saldo de la cuota y de la venta fue actualizado.', 'info')
    return redirect(url_for('main.cxc_vista'))

@bp.route('/cancelar-cuota-cxc/<int:id>', methods=['POST'])
@login_required
def cancelar_cuota_cxc(id):
    """Pág. 32: Cancelar cuota (solo cuando Pagado = 0) con motivo obligatorio."""
    cxc = CuentaPorCobrar.query.get_or_404(id)
    if (cxc.pagado or 0) > 0:
        flash('Devuelva los pagos antes de cancelar la cuota.', 'danger')
        return redirect(url_for('main.cxc_vista'))

    motivo = request.form.get('motivo', '').strip()
    if not motivo:
        flash('Debe indicar el motivo para cancelar la cuota.', 'danger')
        return redirect(url_for('main.cxc_vista'))

    cxc.estado = 'Cancelado'
    cxc.saldo = 0.0
    cxc.motivo_cancelacion = motivo
    if cxc.venta_id:
        venta = Venta.query.get(cxc.venta_id)
        if venta:
            recalcular_venta(venta)
    db.session.commit()
    flash(f'Cuota "{cxc.concepto}" cancelada correctamente.', 'warning')
    return redirect(url_for('main.cxc_vista'))

# ==============================================================================
# 4. CUENTAS A PAGAR (Páginas 27, 28 y 34)
# ==============================================================================

@bp.route('/cuentas-a-pagar')
@login_required
def cxp_vista():
    if current_user.username.lower() == 'recepcion':
        return redirect(url_for('main.ventas_vista'))

    for p_init in CuentaPorPagar.query.all():
        if (p_init.saldo is None or p_init.saldo == 0) and p_init.estado == 'Pendiente' and (p_init.pagado or 0) == 0:
            p_init.saldo = p_init.monto
    db.session.commit()

    q = request.args.get('q', '').strip()
    categoria_id = request.args.get('categoria_id', type=int)
    metodo_pago = request.args.get('metodo_pago', '').strip()
    cuenta_id = request.args.get('cuenta_id', type=int)
    fecha_desde_str = request.args.get('fecha_desde', '')
    fecha_hasta_str = request.args.get('fecha_hasta', '')

    query = CuentaPorPagar.query
    if q:
        query = query.filter(CuentaPorPagar.proveedor.ilike(f'%{q}%') | CuentaPorPagar.concepto.ilike(f'%{q}%'))
    if categoria_id:
        query = query.filter_by(categoria_id=categoria_id)
    if metodo_pago:
        query = query.filter(CuentaPorPagar.metodo_pago.ilike(f'%{metodo_pago}%'))
    if cuenta_id:
        query = query.filter_by(cuenta_id=cuenta_id)
    if fecha_desde_str:
        query = query.filter(CuentaPorPagar.fecha_vencimiento >= datetime.strptime(fecha_desde_str, '%Y-%m-%d'))
    if fecha_hasta_str:
        dt_h = datetime.strptime(fecha_hasta_str, '%Y-%m-%d')
        query = query.filter(CuentaPorPagar.fecha_vencimiento <= datetime.combine(dt_h.date(), time.max))

    cxp_list = query.order_by(CuentaPorPagar.fecha_vencimiento.asc(), CuentaPorPagar.id.asc()).all()
    cuentas = Cuenta.query.filter((Cuenta.activo == True) | (Cuenta.activo.is_(None))).all()
    categorias = Categoria.query.filter((Categoria.activo == True) | (Categoria.activo.is_(None))).all()
    bloqueos_activos = BloqueoPeriodo.query.filter_by(estado='Bloqueado').all()

    return render_template('cxp.html',
                           cxp_list=cxp_list,
                           cuentas=cuentas,
                           categorias=categorias,
                           bloqueos_activos=bloqueos_activos,
                           hoy_str=date.today().strftime('%Y-%m-%d'),
                           selected_q=q,
                           selected_categoria=categoria_id,
                           selected_metodo=metodo_pago,
                           selected_cuenta=cuenta_id,
                           selected_desde=fecha_desde_str,
                           selected_hasta=fecha_hasta_str)

@bp.route('/exportar-cxp-excel')
@login_required
def exportar_cxp_excel():
    """Pág. 28: Exportar cuentas_a_pagar_aaaa-mm-dd.xlsx respetando filtros activos."""
    q = request.args.get('q', '').strip()
    categoria_id = request.args.get('categoria_id', type=int)
    metodo_pago = request.args.get('metodo_pago', '').strip()
    cuenta_id = request.args.get('cuenta_id', type=int)
    fecha_desde_str = request.args.get('fecha_desde', '')
    fecha_hasta_str = request.args.get('fecha_hasta', '')

    query = CuentaPorPagar.query
    if q:
        query = query.filter(CuentaPorPagar.proveedor.ilike(f'%{q}%') | CuentaPorPagar.concepto.ilike(f'%{q}%'))
    if categoria_id:
        query = query.filter_by(categoria_id=categoria_id)
    if metodo_pago:
        query = query.filter(CuentaPorPagar.metodo_pago.ilike(f'%{metodo_pago}%'))
    if cuenta_id:
        query = query.filter_by(cuenta_id=cuenta_id)
    if fecha_desde_str:
        query = query.filter(CuentaPorPagar.fecha_vencimiento >= datetime.strptime(fecha_desde_str, '%Y-%m-%d'))
    if fecha_hasta_str:
        dt_h = datetime.strptime(fecha_hasta_str, '%Y-%m-%d')
        query = query.filter(CuentaPorPagar.fecha_vencimiento <= datetime.combine(dt_h.date(), time.max))

    cxp_list = query.order_by(CuentaPorPagar.fecha_vencimiento.asc(), CuentaPorPagar.id.asc()).all()
    if not cxp_list:
        flash('No hay registros para exportar.', 'warning')
        return redirect(url_for('main.cxp_vista'))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Cuentas a Pagar"
    ws.append(['Proveedor', 'Concepto', 'Categoría', 'Vencimiento', 'Moneda', 'Total', 'Pagado', 'Saldo', 'Estado', 'Forma de pago', 'Cuenta'])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")

    for p in cxp_list:
        ws.append([
            p.proveedor,
            p.concepto,
            p.categoria.nombre if p.categoria else 'Sin Categoría',
            p.fecha_vencimiento.strftime('%d/%m/%Y') if p.fecha_vencimiento else '',
            p.moneda or 'PYG',
            round(p.monto or 0, 2),
            round(p.pagado or 0, 2),
            round(p.saldo or 0, 2),
            p.estado,
            p.metodo_pago if (p.pagado or 0) > 0 else '-',
            p.cuenta_nombre if (p.pagado or 0) > 0 else '-'
        ])
    for row in ws.iter_rows(min_row=2):
        fmt = '#,##0' if row[4].value == 'PYG' else '#,##0.00'
        for col_idx in (5, 6, 7):
            row[col_idx].number_format = fmt

    _autoajustar_columnas_xlsx(ws)
    out = BytesIO()
    wb.save(out)
    out.seek(0)
    fecha_hoy = date.today().strftime('%Y-%m-%d')
    return Response(out.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment;filename=cuentas_a_pagar_{fecha_hoy}.xlsx"})

@bp.route('/agregar-cxp', methods=['POST'])
@login_required
def agregar_cxp():
    if current_user.username.lower() == 'recepcion':
        flash('Acceso no autorizado.', 'danger')
        return redirect(url_for('main.cxp_vista'))
    
    proveedor = request.form.get('proveedor')
    concepto = request.form.get('concepto')
    categoria_id = int(request.form.get('categoria_id')) if request.form.get('categoria_id') else None
    moneda = request.form.get('moneda', 'PYG')
    metodo_pago = request.form.get('metodo_pago', '-')
    monto = limpiar_monto(request.form.get('monto') or 0)
    fecha_venc_str = request.form.get('fecha_vencimiento')
    fecha_vencimiento = datetime.strptime(fecha_venc_str, '%Y-%m-%d') if fecha_venc_str else datetime.utcnow()

    if proveedor and monto > 0:
        db.session.add(CuentaPorPagar(
            proveedor=proveedor,
            concepto=concepto,
            categoria_id=categoria_id,
            moneda=moneda,
            metodo_pago='-',
            cuenta_nombre='-',
            monto=monto,
            pagado=0.0,
            saldo=monto,
            fecha_factura=datetime.utcnow(),
            fecha_vencimiento=fecha_vencimiento,
            estado='Pendiente'
        ))
        db.session.commit()
        flash('Cuenta a pagar registrada exitosamente.', 'success')
        
    return redirect(url_for('main.cxp_vista'))

@bp.route('/pagar-cxp/<int:id>', methods=['POST'])
@login_required
def pagar_cxp(id):
    """Pág. 34: Pago de CxP con soporte de pago parcial, multimoneda, fecha y bloqueo."""
    if current_user.username.lower() == 'recepcion':
        flash('Acceso no autorizado.', 'danger')
        return redirect(url_for('main.cxp_vista'))
    
    cxp = CuentaPorPagar.query.get_or_404(id)
    cuenta_id = request.form.get('cuenta_id', type=int)
    moneda_pago = request.form.get('moneda_pago', cxp.moneda or 'PYG')
    cotizacion = limpiar_monto(request.form.get('cotizacion') or 1.0)
    monto_pagado = limpiar_monto(request.form.get('monto_pagado') or cxp.saldo or cxp.monto)
    forma_pago = request.form.get('forma_pago', 'Transferencia')
    fecha_str = request.form.get('fecha_pago')
    fecha_pago = datetime.strptime(fecha_str, '%Y-%m-%d') if fecha_str else datetime.utcnow()

    if not cuenta_id:
        flash('Debe elegir una cuenta de salida.', 'danger')
        return redirect(url_for('main.cxp_vista'))

    cuenta = Cuenta.query.get_or_404(cuenta_id)
    if cuenta.moneda != moneda_pago:
        flash(f'La cuenta "{cuenta.nombre}" ({cuenta.moneda}) no coincide con la moneda seleccionada ({moneda_pago}).', 'danger')
        return redirect(url_for('main.cxp_vista'))

    msg_bloq = verificar_bloqueo_fecha(fecha_pago, cuenta.id)
    if msg_bloq:
        flash(msg_bloq, 'danger')
        return redirect(url_for('main.cxp_vista'))

    if moneda_pago == 'PYG' or cotizacion <= 0:
        cotizacion = 1.0

    equivalente_gs = monto_pagado if cuenta.moneda == 'PYG' else round(monto_pagado * cotizacion, 0)

    if monto_pagado > 0 and cxp.estado != 'Pagado':
        db.session.add(Transaccion(
            descripcion=f"Pago CxP: {cxp.concepto} ({cxp.proveedor})",
            tipo='Salida',
            moneda=cuenta.moneda,
            monto=monto_pagado,
            cotizacion=cotizacion,
            equivalente_gs=equivalente_gs,
            cuenta_id=cuenta.id,
            categoria_id=cxp.categoria_id,
            cxp_id=cxp.id,
            metodo_pago=forma_pago,
            conciliado=False,
            fecha=fecha_pago,
            observacion=f"Pago a {cxp.proveedor} | Moneda: {cuenta.moneda}",
            usuario=current_user.username
        ))
        db.session.flush()
        recalcular_estado_cxp(cxp)
        db.session.commit()
        flash(f'Pago registrado correctamente para {cxp.proveedor}.', 'success')
        
    return redirect(url_for('main.cxp_vista'))

@bp.route('/devolver-pago-cxp/<int:id>', methods=['POST'])
@login_required
def devolver_pago_cxp(id):
    """Pág. 34: Devolver pago en CxP crea un Ingreso en Finanzas y reabre el saldo del título."""
    cxp = CuentaPorPagar.query.get_or_404(id)
    tx_origen_id = request.form.get('transaccion_id', type=int)
    monto_devolver = limpiar_monto(request.form.get('monto_devolver') or 0.0)
    cuenta_id = request.form.get('cuenta_id', type=int)
    motivo = request.form.get('motivo', '').strip()
    fecha_str = request.form.get('fecha_devolucion')
    fecha_dev = datetime.strptime(fecha_str, '%Y-%m-%d') if fecha_str else datetime.utcnow()

    if not motivo:
        flash('El motivo de la devolución es obligatorio.', 'danger')
        return redirect(url_for('main.cxp_vista'))

    tx_orig = Transaccion.query.get(tx_origen_id) if tx_origen_id else None
    cuenta = Cuenta.query.get_or_404(cuenta_id or (tx_orig.cuenta_id if tx_orig else cxp.cuenta_id))

    msg_bloq = verificar_bloqueo_fecha(fecha_dev, cuenta.id)
    if msg_bloq:
        flash(msg_bloq, 'danger')
        return redirect(url_for('main.cxp_vista'))

    cotizacion = tx_orig.cotizacion if (tx_orig and tx_orig.cotizacion) else 1.0
    equivalente_gs = monto_devolver if cuenta.moneda == 'PYG' else round(monto_devolver * cotizacion, 0)

    db.session.add(Transaccion(
        descripcion=f"Devolución de pago CxP: {cxp.concepto} ({cxp.proveedor}) - {motivo}",
        tipo='Ingreso',
        moneda=cuenta.moneda,
        monto=monto_devolver,
        cotizacion=cotizacion,
        equivalente_gs=equivalente_gs,
        cuenta_id=cuenta.id,
        categoria_id=cxp.categoria_id,
        cxp_id=cxp.id,
        metodo_pago=tx_orig.metodo_pago if tx_orig else 'Transferencia',
        fecha=fecha_dev,
        observacion=f"Devolución proveedor: {motivo}",
        es_devolucion=True,
        usuario=current_user.username
    ))
    db.session.flush()
    recalcular_estado_cxp(cxp)
    db.session.commit()
    flash('Devolución de pago registrada en Finanzas y saldo de CxP actualizado.', 'info')
    return redirect(url_for('main.cxp_vista'))

@bp.route('/cancelar-cxp/<int:id>', methods=['POST'])
@login_required
def cancelar_cxp(id):
    """Pág. 34: Cancelar cuenta a pagar cuando Pagado = 0 con motivo obligatorio."""
    cxp = CuentaPorPagar.query.get_or_404(id)
    if (cxp.pagado or 0) > 0:
        flash('Devuelva los pagos antes de cancelar esta cuenta a pagar.', 'danger')
        return redirect(url_for('main.cxp_vista'))

    motivo = request.form.get('motivo', '').strip()
    if not motivo:
        flash('El motivo de cancelación es obligatorio.', 'danger')
        return redirect(url_for('main.cxp_vista'))

    cxp.estado = 'Cancelado'
    cxp.saldo = 0.0
    cxp.motivo_cancelacion = motivo
    db.session.commit()
    flash('Cuenta a pagar cancelada correctamente.', 'warning')
    return redirect(url_for('main.cxp_vista'))

@bp.route('/eliminar-cxp/<int:id>', methods=['POST'])
@login_required
def eliminar_cxp(id):
    if current_user.username.lower() == 'recepcion':
        flash('Acceso no autorizado.', 'danger')
        return redirect(url_for('main.cxp_vista'))
    cxp = CuentaPorPagar.query.get_or_404(id)
    if (cxp.pagado or 0) > 0:
        flash('No se puede eliminar una cuenta con pagos registrados. Devuelva el pago primero.', 'danger')
        return redirect(url_for('main.cxp_vista'))
    db.session.delete(cxp)
    db.session.commit()
    flash('Cuenta a pagar eliminada correctamente.', 'success')
    return redirect(url_for('main.cxp_vista'))

# ==============================================================================
# 5. CADASTROS Y REGISTROS MAESTROS (Páginas 22 a 25)
# ==============================================================================

@bp.route('/cadastros')
@login_required
def cadastros():
    try:
        cuentas = Cuenta.query.filter((Cuenta.activo == True) | (Cuenta.activo.is_(None))).all()
        clientes = Cliente.query.filter((Cliente.activo == True) | (Cliente.activo.is_(None))).all()
        medicos = Medico.query.filter((Medico.activo == True) | (Medico.activo.is_(None))).all()
        servicios = Servicio.query.filter((Servicio.activo == True) | (Servicio.activo.is_(None))).all()
        paquetes = Paquete.query.filter((Paquete.activo == True) | (Paquete.activo.is_(None))).all()
        cxc_list = CuentaPorCobrar.query.all()
        cxp_list = CuentaPorPagar.query.all()
        categorias = Categoria.query.filter((Categoria.activo == True) | (Categoria.activo.is_(None))).all()
        centros_costo = CentroCosto.query.filter((CentroCosto.activo == True) | (CentroCosto.activo.is_(None))).all()
        
        return render_template('cadastros.html', 
                               cuentas=cuentas, 
                               clientes=clientes, 
                               medicos=medicos, 
                               servicios=servicios,
                               paquetes=paquetes,
                               cxc_list=cxc_list,
                               cxp_list=cxp_list,
                               categorias=categorias,
                               centros_costo=centros_costo)
    except Exception:
        return f"<div style='padding:25px;font-family:monospace;background:#fff0f0;color:#b00020;border:2px solid #b00020;margin:20px;border-radius:8px;'><h3>Diagnóstico de Error en /cadastros:</h3><pre>{traceback.format_exc()}</pre></div>", 200

@bp.route('/agregar-cuenta', methods=['POST'])
@login_required
def agregar_cuenta():
    if current_user.username.lower() == 'recepcion':
        flash('Acceso no autorizado.', 'danger')
        return redirect(url_for('main.cadastros'))
    nombre = request.form.get('nombre')
    tipo_cuenta = request.form.get('tipo_cuenta')
    moneda = request.form.get('moneda', 'PYG')
    saldo_inicial = limpiar_monto(request.form.get('saldo_inicial') or 0)
    color = request.form.get('color', '#17b978')
    db.session.add(Cuenta(nombre=nombre, tipo_cuenta=tipo_cuenta, moneda=moneda, saldo_inicial=saldo_inicial, color=color, activo=True))
    db.session.commit()
    flash('Cuenta creada con éxito.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/editar-cuenta/<int:id>', methods=['POST'])
@login_required
def editar_cuenta(id):
    if current_user.username.lower() == 'recepcion':
        flash('Acceso no autorizado.', 'danger')
        return redirect(url_for('main.cadastros'))
    c = Cuenta.query.get_or_404(id)
    c.nombre = request.form.get('nombre', c.nombre)
    c.tipo_cuenta = request.form.get('tipo_cuenta', c.tipo_cuenta)
    if request.form.get('color'):
        c.color = request.form.get('color')

    if request.form.get('saldo_inicial') is not None:
        nuevo_saldo = limpiar_monto(request.form.get('saldo_inicial'))
        if nuevo_saldo != (c.saldo_inicial or 0.0):
            for tx_c in c.transacciones:
                if verificar_bloqueo_fecha(tx_c.fecha, c.id):
                    flash('No se puede cambiar el Saldo Inicial porque la cuenta tiene transacciones en un período bloqueado.', 'danger')
                    return redirect(url_for('main.cadastros'))
            c.saldo_inicial = nuevo_saldo

    if not c.transacciones and request.form.get('moneda'):
        c.moneda = request.form.get('moneda', c.moneda)

    db.session.commit()
    flash('Cuenta actualizada correctamente y saldos recalculados.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/eliminar-cuenta/<int:id>', methods=['POST'])
@login_required
def eliminar_cuenta(id):
    if current_user.username.lower() == 'recepcion':
        flash('Acceso no autorizado.', 'danger')
        return redirect(url_for('main.cadastros'))
    c = Cuenta.query.get_or_404(id)
    cant_uso = Transaccion.query.filter_by(cuenta_id=id).count()
    if cant_uso > 0:
        c.activo = False
        db.session.commit()
        flash(f'La cuenta "{c.nombre}" tiene {cant_uso} movimiento(s) asociado(s). Se ha inactivado para conservar el historial.', 'warning')
    else:
        db.session.delete(c)
        db.session.commit()
        flash('Cuenta eliminada correctamente.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/agregar-categoria', methods=['POST'])
@login_required
def agregar_categoria():
    if current_user.username.lower() == 'recepcion':
        flash('Acceso no autorizado.', 'danger')
        return redirect(url_for('main.cadastros'))
    nombre = request.form.get('nombre')
    tipo = request.form.get('tipo', 'Ingreso')
    if nombre:
        db.session.add(Categoria(nombre=nombre, tipo=tipo, activo=True))
        db.session.commit()
        flash('Categoría agregada con éxito.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/editar-categoria/<int:id>', methods=['POST'])
@login_required
def editar_categoria(id):
    if current_user.username.lower() == 'recepcion':
        flash('Acceso no autorizado.', 'danger')
        return redirect(url_for('main.cadastros'))
    cat = Categoria.query.get_or_404(id)
    cat.nombre = request.form.get('nombre')
    cat.tipo = request.form.get('tipo', 'Ingreso')
    db.session.commit()
    flash('Categoría actualizada correctamente.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/eliminar-categoria/<int:id>', methods=['POST'])
@login_required
def eliminar_categoria(id):
    if current_user.username.lower() == 'recepcion':
        flash('Acceso no autorizado.', 'danger')
        return redirect(url_for('main.cadastros'))
    cat = Categoria.query.get_or_404(id)
    cant_uso = Transaccion.query.filter_by(categoria_id=id).count() + CuentaPorPagar.query.filter_by(categoria_id=id).count()
    if cant_uso > 0:
        cat.activo = False
        db.session.commit()
        flash(f'La categoría "{cat.nombre}" tiene {cant_uso} movimiento(s) asociado(s). Se inactivó para preservar el historial.', 'warning')
    else:
        db.session.delete(cat)
        db.session.commit()
        flash('Categoría eliminada correctamente.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/agregar-centro-costo', methods=['POST'])
@login_required
def agregar_centro_costo():
    if current_user.username.lower() == 'recepcion':
        flash('Acceso no autorizado.', 'danger')
        return redirect(url_for('main.cadastros'))
    nombre = request.form.get('nombre')
    if nombre:
        db.session.add(CentroCosto(nombre=nombre, activo=True))
        db.session.commit()
        flash('Centro de costo agregado con éxito.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/editar-centro-costo/<int:id>', methods=['POST'])
@login_required
def editar_centro_costo(id):
    if current_user.username.lower() == 'recepcion':
        flash('Acceso no autorizado.', 'danger')
        return redirect(url_for('main.cadastros'))
    cc = CentroCosto.query.get_or_404(id)
    cc.nombre = request.form.get('nombre')
    db.session.commit()
    flash('Centro de costo actualizado correctamente.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/eliminar-centro-costo/<int:id>', methods=['POST'])
@login_required
def eliminar_centro_costo(id):
    """Pág. 24: Si el Centro de Costo está en uso, no se borra: se inactiva e informa cuántos movimientos lo usan."""
    if current_user.username.lower() == 'recepcion':
        flash('Acceso no autorizado.', 'danger')
        return redirect(url_for('main.cadastros'))
    cc = CentroCosto.query.get_or_404(id)
    cant_uso = Transaccion.query.filter_by(centro_costo_id=id).count()
    if cant_uso > 0:
        cc.activo = False
        db.session.commit()
        flash(f'"{cc.nombre}" tiene {cant_uso} movimiento(s) asociado(s). Fue inactivado para conservar el historial.', 'warning')
    else:
        db.session.delete(cc)
        db.session.commit()
        flash('Centro de costo eliminado correctamente.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/agregar-paquete', methods=['POST'])
@login_required
def agregar_paquete():
    if current_user.username.lower() == 'recepcion':
        flash('Acceso no autorizado para crear paquetes.', 'danger')
        return redirect(url_for('main.cadastros'))
    nombre = request.form.get('nombre')
    moneda = request.form.get('moneda', 'PYG')
    precio = limpiar_monto(request.form.get('precio', '0'))
    duracion_meses = int(request.form.get('duracion_meses') or 0)
    
    paquete = Paquete(nombre=nombre, moneda=moneda, precio=precio, duracion_meses=duracion_meses, activo=True)
    db.session.add(paquete)
    db.session.flush()
    
    servicio_ids = request.form.getlist('servicio_id[]')
    cantidades = request.form.getlist('cantidad[]')
    costos_unitarios = request.form.getlist('costo_unitario[]')
    repartos = request.form.getlist('reparto_adicional[]')
    
    for i in range(len(servicio_ids)):
        if servicio_ids[i]:
            db.session.add(PaqueteItem(
                paquete_id=paquete.id,
                servicio_id=int(servicio_ids[i]),
                cantidad=int(cantidades[i]) if i < len(cantidades) and cantidades[i] else 1,
                costo_unitario=limpiar_monto(costos_unitarios[i]) if i < len(costos_unitarios) and costos_unitarios[i] else 0.0,
                reparto_adicional=limpiar_monto(repartos[i]) if i < len(repartos) and repartos[i] else 0.0
            ))
            
    obtener_o_crear_categoria_plan(nombre)
    db.session.commit()
    flash('Plan o paquete creado exitosamente.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/editar-paquete/<int:id>', methods=['POST'])
@login_required
def editar_paquete(id):
    """Pág. 25: Editar plan agrega/quita servicios sin alterar las ventas ya registradas."""
    if current_user.username.lower() == 'recepcion':
        flash('Acceso no autorizado.', 'danger')
        return redirect(url_for('main.cadastros'))
    p = Paquete.query.get_or_404(id)
    p.nombre = request.form.get('nombre')
    p.precio = limpiar_monto(request.form.get('precio', '0'))
    p.duracion_meses = int(request.form.get('duracion_meses') or 0)
    
    PaqueteItem.query.filter_by(paquete_id=p.id).delete()
    
    servicio_ids = request.form.getlist('servicio_id[]')
    cantidades = request.form.getlist('cantidad[]')
    costos_unitarios = request.form.getlist('costo_unitario[]')
    repartos = request.form.getlist('reparto_adicional[]')
    
    for i in range(len(servicio_ids)):
        if servicio_ids[i]:
            db.session.add(PaqueteItem(
                paquete_id=p.id,
                servicio_id=int(servicio_ids[i]),
                cantidad=int(cantidades[i]) if i < len(cantidades) and cantidades[i] else 1,
                costo_unitario=limpiar_monto(costos_unitarios[i]) if i < len(costos_unitarios) and costos_unitarios[i] else 0.0,
                reparto_adicional=limpiar_monto(repartos[i]) if i < len(repartos) and repartos[i] else 0.0
            ))
            
    cant_ventas = Venta.query.filter_by(paquete_id=p.id).count()
    db.session.commit()
    flash(f'Plan "{p.nombre}" actualizado para ventas nuevas. Las {cant_ventas} venta(s) ya registrada(s) con este plan no se modifican.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/eliminar-paquete/<int:id>', methods=['POST'])
@login_required
def eliminar_paquete(id):
    if current_user.username.lower() == 'recepcion':
        flash('Acceso no autorizado.', 'danger')
        return redirect(url_for('main.cadastros'))
    p = Paquete.query.get_or_404(id)
    cant_ventas = Venta.query.filter_by(paquete_id=p.id).count()
    if cant_ventas > 0:
        p.activo = False
        db.session.commit()
        flash(f'El plan "{p.nombre}" tiene {cant_ventas} venta(s) asociada(s). Fue inactivado para conservar el historial.', 'warning')
    else:
        db.session.delete(p)
        db.session.commit()
        flash('Plan o paquete eliminado correctamente.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/agregar-cliente', methods=['POST'])
@login_required
def agregar_cliente():
    nombre = request.form.get('nombre')
    documento = request.form.get('documento')
    telefono = request.form.get('telefono')
    email = request.form.get('email')
    fecha_nac_str = request.form.get('fecha_nacimiento')
    fecha_nacimiento = datetime.strptime(fecha_nac_str, '%Y-%m-%d').date() if fecha_nac_str else None

    db.session.add(Cliente(
        nombre=nombre, 
        documento=documento, 
        telefono=telefono, 
        email=email, 
        fecha_nacimiento=fecha_nacimiento,
        activo=True
    ))
    db.session.commit()
    flash('Cliente registrado exitosamente.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/editar-cliente/<int:id>', methods=['POST'])
@login_required
def editar_cliente(id):
    cl = Cliente.query.get_or_404(id)
    cl.nombre = request.form.get('nombre')
    cl.documento = request.form.get('documento')
    cl.telefono = request.form.get('telefono')
    cl.email = request.form.get('email')
    fecha_nac_str = request.form.get('fecha_nacimiento')
    cl.fecha_nacimiento = datetime.strptime(fecha_nac_str, '%Y-%m-%d').date() if fecha_nac_str else None

    db.session.commit()
    flash('Cliente actualizado correctamente.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/agregar-cliente-ajax', methods=['POST'])
@login_required
def agregar_cliente_ajax():
    nombre = request.form.get('nombre')
    telefono = request.form.get('telefono')
    if nombre:
        cl = Cliente(nombre=nombre, telefono=telefono, activo=True)
        db.session.add(cl)
        db.session.commit()
        return {'success': True, 'id': cl.id, 'nombre': cl.nombre}
    return {'success': False}, 400

@bp.route('/eliminar-cliente/<int:id>', methods=['POST'])
@login_required
def eliminar_cliente(id):
    cl = Cliente.query.get_or_404(id)
    en_uso = Venta.query.filter_by(cliente_id=id).count() + Transaccion.query.filter_by(cliente_id=id).count()
    if en_uso > 0:
        cl.activo = False
        db.session.commit()
        flash(f'El paciente "{cl.nombre}" tiene {en_uso} registro(s) asociado(s). Fue inactivado para conservar el historial.', 'warning')
    else:
        db.session.delete(cl)
        db.session.commit()
        flash('Cliente eliminado correctamente.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/agregar-medico', methods=['POST'])
@login_required
def agregar_medico():
    if current_user.username.lower() == 'recepcion':
        flash('Acceso no autorizado.', 'danger')
        return redirect(url_for('main.cadastros'))
    nombre = request.form.get('nombre')
    especialidad = request.form.get('especialidad')
    porcentaje_medico = limpiar_monto(request.form.get('porcentaje_medico') or 70)
    porcentaje_clinica = limpiar_monto(request.form.get('porcentaje_clinica') or 30)
    db.session.add(Medico(nombre=nombre, especialidad=especialidad, porcentaje_medico=porcentaje_medico, porcentaje_clinica=porcentaje_clinica, activo=True))
    db.session.commit()
    flash('Profesional médico agregado con éxito.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/editar-medico/<int:id>', methods=['POST'])
@login_required
def editar_medico(id):
    if current_user.username.lower() == 'recepcion':
        flash('Acceso no autorizado.', 'danger')
        return redirect(url_for('main.cadastros'))
    m = Medico.query.get_or_404(id)
    m.nombre = request.form.get('nombre')
    m.especialidad = request.form.get('especialidad')
    m.porcentaje_medico = limpiar_monto(request.form.get('porcentaje_medico') or 70)
    m.porcentaje_clinica = limpiar_monto(request.form.get('porcentaje_clinica') or 30)
    db.session.commit()
    flash('Médico actualizado correctamente.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/eliminar-medico/<int:id>', methods=['POST'])
@login_required
def eliminar_medico(id):
    if current_user.username.lower() == 'recepcion':
        flash('Acceso no autorizado.', 'danger')
        return redirect(url_for('main.cadastros'))
    m = Medico.query.get_or_404(id)
    en_uso = PrestacionServicio.query.filter_by(medico_id=id).count()
    if en_uso > 0:
        m.activo = False
        db.session.commit()
        flash(f'El profesional "{m.nombre}" tiene {en_uso} prestación(es) asociada(s). Fue inactivado para conservar el historial.', 'warning')
    else:
        db.session.delete(m)
        db.session.commit()
        flash('Profesional eliminado correctamente.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/agregar-servicio', methods=['POST'])
@login_required
def agregar_servicio():
    if current_user.username.lower() == 'recepcion':
        flash('Acceso no autorizado.', 'danger')
        return redirect(url_for('main.cadastros'))
    codigo = request.form.get('codigo')
    nombre = request.form.get('nombre')
    descripcion = request.form.get('descripcion')
    moneda = request.form.get('moneda', 'PYG')
    precio = limpiar_monto(request.form.get('precio') or 0)
    costo_unitario = limpiar_monto(request.form.get('costo_unitario') or 0)
    profesional_asociado = request.form.get('profesional_asociado')
    reparto_adicional = limpiar_monto(request.form.get('reparto_adicional') or 0)
    
    db.session.add(Servicio(
        codigo=codigo, 
        nombre=nombre, 
        descripcion=descripcion, 
        moneda=moneda, 
        precio=precio, 
        costo_unitario=costo_unitario,
        profesional_asociado=profesional_asociado,
        reparto_adicional=reparto_adicional,
        activo=True
    ))
    db.session.commit()
    flash('Servicio registrado exitosamente.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/editar-servicio/<int:id>', methods=['POST'])
@login_required
def editar_servicio(id):
    if current_user.username.lower() == 'recepcion':
        flash('Acceso no autorizado.', 'danger')
        return redirect(url_for('main.cadastros'))
    s = Servicio.query.get_or_404(id)
    s.nombre = request.form.get('nombre')
    s.moneda = request.form.get('moneda', 'PYG')
    s.precio = limpiar_monto(request.form.get('precio') or 0)
    s.costo_unitario = limpiar_monto(request.form.get('costo_unitario') or 0)
    s.profesional_asociado = request.form.get('profesional_asociado')
    s.reparto_adicional = limpiar_monto(request.form.get('reparto_adicional') or 0)
    db.session.commit()
    flash('Servicio actualizado correctamente (aplica para ventas nuevas).', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/eliminar-servicio/<int:id>', methods=['POST'])
@login_required
def eliminar_servicio(id):
    if current_user.username.lower() == 'recepcion':
        flash('Acceso no autorizado.', 'danger')
        return redirect(url_for('main.cadastros'))
    s = Servicio.query.get_or_404(id)
    en_uso = PaqueteItem.query.filter_by(servicio_id=id).count() + DetalleVenta.query.filter_by(servicio_id=id).count()
    if en_uso > 0:
        s.activo = False
        db.session.commit()
        flash(f'El servicio "{s.nombre}" está vinculado a planes o ventas existentes. Fue inactivado para conservar el historial.', 'warning')
    else:
        db.session.delete(s)
        db.session.commit()
        flash('Servicio eliminado correctamente.', 'success')
    return redirect(url_for('main.cadastros'))

# ==============================================================================
# 6. HONORARIOS Y FLUJO PROYECTADO
# ==============================================================================

@bp.route('/honorarios')
@login_required
def honorarios():
    if current_user.username.lower() == 'recepcion':
        return redirect(url_for('main.ventas_vista'))
    medicos = Medico.query.filter((Medico.activo == True) | (Medico.activo.is_(None))).all()
    servicios = Servicio.query.filter((Servicio.activo == True) | (Servicio.activo.is_(None))).all()
    clientes = Cliente.query.filter((Cliente.activo == True) | (Cliente.activo.is_(None))).all()
    prestaciones = PrestacionServicio.query.order_by(PrestacionServicio.fecha.desc()).all()
    
    prestaciones_virtuales = []
    ventas = Venta.query.filter(Venta.estado != 'Cancelada').all()
    for v in ventas:
        if v.paquete and v.paquete.items:
            for item in v.paquete.items:
                nombre_srv = (item.servicio.nombre if item.servicio else '').strip().lower()
                for m in medicos:
                    esp = (m.especialidad or '').strip().lower()
                    if (esp and (esp in nombre_srv or nombre_srv in esp)) or ('nutricion' in esp and 'nutricionista' in nombre_srv):
                        porc_med = (m.porcentaje_medico or 70.0) / 100.0
                        val_total = item.costo_total_item if (item.costo_total_item or 0) > 0 else ((item.servicio.precio if item.servicio else 0.0) * (item.cantidad or 1))
                        prestaciones_virtuales.append({
                            'fecha': v.fecha,
                            'medico': m,
                            'servicio_nombre': item.servicio.nombre if item.servicio else 'Servicio',
                            'cliente': v.cliente,
                            'valor_total': val_total,
                            'honorario': val_total * porc_med,
                            'clinica': val_total * (1 - porc_med)
                        })

    return render_template('honorarios.html', medicos=medicos, servicios=servicios, clientes=clientes, prestaciones=prestaciones, prestaciones_virtuales=prestaciones_virtuales)

@bp.route('/agregar-prestacion', methods=['POST'])
@login_required
def agregar_prestacion():
    medico_id = int(request.form.get('medico_id')) if request.form.get('medico_id') else None
    servicio_id = int(request.form.get('servicio_id')) if request.form.get('servicio_id') else None
    cliente_id = int(request.form.get('cliente_id')) if request.form.get('cliente_id') else None
    
    if medico_id and servicio_id:
        medico = Medico.query.get(medico_id)
        servicio = Servicio.query.get(servicio_id)
        
        if medico and servicio:
            db.session.add(PrestacionServicio(
                medico_id=medico_id,
                servicio_id=servicio_id,
                cliente_id=cliente_id,
                fecha=datetime.utcnow()
            ))
            porc_med = medico.porcentaje_medico if medico.porcentaje_medico else 70.0
            honorario_medico = servicio.precio * (porc_med / 100.0)
            
            db.session.add(CuentaPorPagar(
                proveedor=f"Dr(a). {medico.nombre}",
                concepto=f"Honorarios: {servicio.nombre}",
                moneda=servicio.moneda,
                metodo_pago='-',
                cuenta_nombre='-',
                monto=honorario_medico,
                pagado=0.0,
                saldo=honorario_medico,
                fecha_factura=datetime.utcnow(),
                fecha_vencimiento=datetime.utcnow(),
                estado='Pendiente'
            ))
            db.session.commit()
            flash('Prestación médica registrada con éxito.', 'success')
            
    return redirect(url_for('main.honorarios'))

@bp.route('/eliminar-prestacion/<int:id>', methods=['POST'])
@login_required
def eliminar_prestacion(id):
    p = PrestacionServicio.query.get_or_404(id)
    db.session.delete(p)
    db.session.commit()
    flash('Prestación eliminada correctamente.', 'success')
    return redirect(url_for('main.honorarios'))

@bp.route('/flujo-proyectado')
@login_required
def flujo_proyectado():
    if current_user.username.lower() == 'recepcion':
        return redirect(url_for('main.ventas_vista'))
    cxc_pendientes = CuentaPorCobrar.query.filter(CuentaPorCobrar.estado.in_(['Pendiente', 'Parcial'])).all()
    cxp_pendientes = CuentaPorPagar.query.filter(CuentaPorPagar.estado.in_(['Pendiente', 'Parcial'])).all()
    total_cxc = sum((x.saldo if (x.saldo is not None and x.saldo > 0) else (x.monto or 0)) for x in cxc_pendientes)
    total_cxp = sum((p.saldo if (p.saldo is not None and p.saldo > 0) else (p.monto or 0)) for p in cxp_pendientes)
    
    ventas_activas = Venta.query.filter(Venta.estado != 'Cancelada').all()
    distribucion_costos = sum(((v.costo_servicios or 0) * 0.25) for v in ventas_activas)
    saldo_proyectado = total_cxc - (total_cxp + distribucion_costos)

    return render_template('flujo_proyectado.html', 
                           cxc_pendientes=cxc_pendientes, 
                           cxp_pendientes=cxp_pendientes, 
                           total_cxc=total_cxc, 
                           total_cxp=total_cxp,
                           distribucion_costos=distribucion_costos,
                           saldo_proyectado=saldo_proyectado)

@bp.route('/reparar-datos-historicos')
@login_required
def reparar_datos_historicos():
    if current_user.username.lower() == 'recepcion':
        return "No autorizado", 403

    eliminadas = 0
    for t in Transaccion.query.all():
        desc_up = (t.descripcion or '').upper()
        es_teste = 'TESTE NADOA' in desc_up
        es_error_usd = (t.monto and t.monto >= 1000000 and t.cuenta and t.cuenta.moneda == 'USD')
        es_transf_100 = ('TRANSFERENCIA' in desc_up and t.monto == 100)
        if es_teste or es_error_usd or es_transf_100:
            cxc_rel = CuentaPorCobrar.query.get(t.cxc_id) if t.cxc_id else None
            cxp_rel = CuentaPorPagar.query.get(t.cxp_id) if t.cxp_id else None
            db.session.delete(t)
            db.session.flush()
            if cxc_rel:
                recalcular_estado_cxc_y_venta(cxc_rel)
            if cxp_rel:
                recalcular_estado_cxp(cxp_rel)
            eliminadas += 1

    cuotas_corregidas = 0
    for cxc in CuentaPorCobrar.query.all():
        if not cxc.venta_id and '(Venta #' in (cxc.concepto or ''):
            try:
                cxc.venta_id = int(cxc.concepto.split('(Venta #')[1].split(')')[0])
            except Exception:
                pass

        if 'Cuota ' in (cxc.concepto or '') and ' de ' in (cxc.concepto or ''):
            try:
                parte_num = cxc.concepto.split('Cuota ')[1].split(' de ')[0].strip()
                num_cuota_idx = int(parte_num)
                total_part = cxc.concepto.split(' de ')[1].split('-')[0].split(' ')[0].strip()
                cxc.numero_cuota_str = f"{num_cuota_idx} de {total_part}"
                if num_cuota_idx > 1 and cxc.fecha_emision:
                    venc_esperado = sumar_meses(cxc.fecha_emision, num_cuota_idx - 1)
                    if not cxc.fecha_vencimiento or cxc.fecha_vencimiento.date() == cxc.fecha_emision.date():
                        cxc.fecha_vencimiento = venc_esperado
                        cuotas_corregidas += 1
            except Exception:
                pass
        recalcular_estado_cxc_y_venta(cxc)

    for v in Venta.query.all():
        recalcular_venta(v)

    db.session.commit()
    flash(f'Datos reparados: {eliminadas} transacción(es) de prueba eliminada(s) y {cuotas_corregidas} fecha(s) de vencimiento escalonada(s) corregida(s).', 'success')
    return redirect(url_for('main.cxc_vista'))

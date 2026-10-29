from flask import Blueprint, render_template, request, redirect, url_for, Response, flash
from flask_login import login_user, logout_user, login_required, current_user
from app import db
from app.models import Cuenta, Transaccion, Cliente, Medico, Servicio, CuentaPorCobrar, CuentaPorPagar, Categoria, CentroCosto, PrestacionServicio, User, Venta, DetalleVenta, Paquete, PaqueteItem, CuotaVenta, BloqueoPeriodo
from datetime import datetime, time
import csv
from io import StringIO

bp = Blueprint('main', __name__)

@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('main.pyg'))
        else:
            flash('Usuario o contraseña incorrectos', 'danger')
            
    return render_template('login.html')

@bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('main.login'))

@bp.route('/')
@login_required
def pyg():
    # Estado de Resultados Gerencial (PyG) según manual
    ventas = Venta.query.filter_by(estado='Pagada').all() # O todas las activas
    total_ingresos_netos = sum((v.total - v.iva) for v in ventas)
    
    costo_total_servicios = sum(v.costo_servicios for v in ventas)
    
    # Costos fijos del mes desde Cuentas a Pagar con categoría de costo fijo
    cxp_fijos = CuentaPorPagar.query.all()
    total_costos_fijos = sum(p.monto for p in cxp_fijos if p.concepto and 'fijo' in p.concepto.lower())
    
    total_egresos_costos = costo_total_servicios + total_costos_fijos
    utilidad_neta = total_ingresos_netos - total_egresos_costos
    
    # Desglose de planes vendidos y valor por profesional
    total_planes = len(ventas)
    valor_por_profesional = []
    medicos = Medico.query.all()
    for m in medicos:
        prestaciones_medico = PrestacionServicio.query.filter_by(medico_id=m.id).count()
        valor_medico = prestaciones_medico * (m.porcentaje_medico * 600000 / 100) if hasattr(m, 'porcentaje_medico') else 0
        valor_por_profesional.append({
            'nombre': m.nombre,
            'especialidad': m.especialidad,
            'consultas': prestaciones_medico,
            'valor': valor_medico
        })

    transacciones = Transaccion.query.all()
    return render_template('pyg.html', 
                           transacciones=transacciones, 
                           total_ingresos=total_ingresos_netos, 
                           total_salidas=total_egresos_costos, 
                           utilidad_neta=utilidad_neta,
                           total_planes=total_planes,
                           valor_por_profesional=valor_por_profesional)

@bp.route('/finanzas')
@login_required
def dashboard():
    q = request.args.get('q', '')
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

    cuentas = Cuenta.query.all()
    saldos = {'PYG': 0.0, 'USD': 0.0, 'BRL': 0.0}
    
    for c in cuentas:
        saldo_cta = c.saldo_inicial
        txs_cta = Transaccion.query.filter_by(cuenta_id=c.id).all()
        for t in txs_cta:
            if t.tipo == 'Ingreso':
                saldo_cta += t.monto
            else:
                saldo_cta -= t.monto
        saldos[c.moneda] += saldo_cta

    saldos_acumulados = {}
    for c in cuentas:
        saldos_acumulados[c.id] = c.saldo_inicial

    txs_cronologicas = Transaccion.query.order_by(Transaccion.fecha.asc(), Transaccion.id.asc()).all()
    for t in txs_cronologicas:
        if t.cuenta_id in saldos_acumulados:
            if t.tipo == 'Ingreso':
                saldos_acumulados[t.cuenta_id] += t.monto
            else:
                saldos_acumulados[t.cuenta_id] -= t.monto
            t.saldo_resultante = saldos_acumulados[t.cuenta_id]
        else:
            t.saldo_resultante = 0.0

    categorias = Categoria.query.all()
    centros_costo = CentroCosto.query.all()
    servicios = Servicio.query.all()
    clientes = Cliente.query.all()
    
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
                           selected_q=q,
                           selected_cuenta=cuenta_id,
                           selected_categoria=categoria_id,
                           selected_centro=centro_id,
                           selected_desde=fecha_desde_str,
                           selected_hasta=fecha_hasta_str)

@bp.route('/bloqueos')
@login_required
def bloqueos_vista():
    bloqueos = BloqueoPeriodo.query.order_by(BloqueoPeriodo.fecha_bloqueo.desc()).all()
    cuentas = Cuenta.query.all()
    return render_template('bloqueos.html', bloqueos=bloqueos, cuentas=cuentas)

@bp.route('/bloquear-periodo', methods=['POST'])
@login_required
def bloquear_periodo():
    periodo = request.form.get('periodo', 'Octubre 2026')
    cuenta_id = request.form.get('cuenta_id')
    cta_val = int(cuenta_id) if cuenta_id and cuenta_id != 'todas' else None
    
    nuevo_bloqueo = BloqueoPeriodo(
        periodo=periodo,
        cuenta_id=cta_val,
        usuario=current_user.username if current_user else 'Administrador',
        estado='Bloqueado'
    )
    db.session.add(nuevo_bloqueo)
    db.session.commit()
    flash('Período bloqueado correctamente.', 'success')
    return redirect(url_for('main.dashboard'))

@bp.route('/desbloquear-periodo/<int:id>', methods=['POST'])
@login_required
def desbloquear_periodo(id):
    b = BloqueoPeriodo.query.get_or_404(id)
    motivo = request.form.get('motivo', '')
    b.estado = 'Liberado'
    b.motivo_desbloqueo = motivo
    db.session.commit()
    flash('Período desbloqueado con éxito.', 'info')
    return redirect(url_for('main.dashboard'))

@bp.route('/desbloquear-todo', methods=['POST'])
@login_required
def desbloquear_todo():
    motivo = request.form.get('motivo', '')
    bloqueos = BloqueoPeriodo.query.filter_by(estado='Bloqueado').all()
    for b in bloqueos:
        b.estado = 'Liberado'
        b.motivo_desbloqueo = motivo
    db.session.commit()
    flash('Todos los bloqueos han sido liberados.', 'warning')
    return redirect(url_for('main.dashboard'))

@bp.route('/ventas')
@login_required
def ventas_vista():
    ventas = Venta.query.order_by(Venta.fecha.desc()).all()
    clientes = Cliente.query.all()
    servicios = Servicio.query.all()
    paquetes = Paquete.query.all()
    cuentas = Cuenta.query.all()
    return render_template('ventas.html', ventas=ventas, clientes=clientes, servicios=servicios, paquetes=paquetes, cuentas=cuentas)

@bp.route('/agregar-venta-completa', methods=['POST'])
@login_required
def agregar_venta_completa():
    cliente_id = int(request.form.get('cliente_id')) if request.form.get('cliente_id') else None
    paquete_id = int(request.form.get('paquete_id')) if request.form.get('paquete_id') else None
    forma_pago = request.form.get('forma_pago', 'Efectivo/Transferencia')
    num_cuotas = int(request.form.get('num_cuotas') or 1)
    monto_pagado = float(request.form.get('monto_pagado') or 0)
    cuenta_id = int(request.form.get('cuenta_id')) if request.form.get('cuenta_id') else None
    
    if paquete_id:
        paquete = Paquete.query.get_or_404(paquete_id)
        precio_total = paquete.precio
        
        iva = precio_total / 11 
        tarifa_pasarela = precio_total * 0.033 if 'Crédito' in forma_pago else 0.0
        costo_servicios = sum(item.costo_total_item for item in paquete.items)
        margen_contribucion = precio_total - iva - tarifa_pasarela - costo_servicios
        costo_fijo_plan = 0.0  
        ganancia_plan = margen_contribucion - costo_fijo_plan
        
        saldo = precio_total - monto_pagado
        estado = 'Pagada' if saldo <= 0 else ('Parcial' if monto_pagado > 0 else 'Pendiente')
        
        venta = Venta(
            fecha=datetime.utcnow(),
            cliente_id=cliente_id,
            paquete_id=paquete.id,
            subtotal=precio_total,
            total=precio_total,
            pagado=monto_pagado,
            saldo=saldo,
            estado=estado,
            iva=iva,
            tarifa_pasarela=tarifa_pasarela,
            costo_servicios=costo_servicios,
            margen_contribucion=margen_contribucion,
            costo_fijo_plan=costo_fijo_plan,
            ganancia_plan=ganancia_plan
        )
        db.session.add(venta)
        db.session.flush() 
        
        monto_cuota = (precio_total - monto_pagado) / num_cuotas if num_cuotas > 0 else precio_total
        vencimientos_labels = ["Día de la venta", "1 mes después", "2 meses después", "3 meses después", "4 meses después"]
        
        for i in range(1, num_cuotas + 1):
            label = vencimientos_labels[i-1] if (i-1) < len(vencimientos_labels) else f"{i-1} meses después"
            num_str = f"{i} de {num_cuotas}"
            
            db.session.add(CuotaVenta(
                venta_id=venta.id,
                numero_cuota=num_str,
                vencimiento_label=label,
                valor=monto_cuota,
                estado='Pendiente'
            ))
            
            if cliente_id:
                db.session.add(CuentaPorCobrar(
                    cliente_id=cliente_id,
                    concepto=f"Cuota {num_str} - {paquete.nombre} (Venta #{venta.id})",
                    moneda='PYG',
                    metodo_pago=forma_pago,
                    monto=monto_cuota,
                    fecha_emision=datetime.utcnow(),
                    fecha_vencimiento=datetime.utcnow(),
                    estado='Pendiente'
                ))
        
        if monto_pagado > 0 and cuenta_id:
            db.session.add(Transaccion(
                descripcion=f"Pago inicial Venta #{venta.id} - {paquete.nombre}",
                tipo='Ingreso',
                monto=monto_pagado,
                cuenta_id=cuenta_id,
                cliente_id=cliente_id,
                metodo_pago=forma_pago,
                conciliado=False,
                fecha=datetime.utcnow()
            ))
            
        db.session.commit()
        flash('Venta registrada exitosamente con generación de cuotas.', 'success')
        
    return redirect(url_for('main.ventas_vista'))

@bp.route('/cancelar-venta/<int:id>', methods=['POST'])
@login_required
def cancelar_venta(id):
    v = Venta.query.get_or_404(id)
    v.estado = 'Cancelada'
    for cuota in v.cuotas:
        if cuota.estado == 'Pendiente':
            cuota.estado = 'Cancelada'
    db.session.commit()
    flash(f'La venta #{v.id} ha sido cancelada correctamente.', 'warning')
    return redirect(url_for('main.ventas_vista'))

@bp.route('/excluir-venta/<int:id>', methods=['POST'])
@login_required
def excluir_venta(id):
    v = Venta.query.get_or_404(id)
    if v.pagado > 0:
        flash(f'No se puede excluir la venta #{v.id} porque tiene cobros registrados. Debe anular los cobros primero o usar "Cancelar venta".', 'danger')
    else:
        for cuota in v.cuotas:
            cxc_rel = CuentaPorCobrar.query.filter_by(concepto=f"Cuota {cuota.numero_cuota} - {v.paquete.nombre if v.paquete else 'Plan'} (Venta #{v.id})").first()
            if cxc_rel:
                db.session.delete(cxc_rel)
        db.session.delete(v)
        db.session.commit()
        flash(f'La venta #{v.id} ha sido excluida del sistema.', 'success')
    return redirect(url_for('main.ventas_vista'))

@bp.route('/registrar-cobro-avanzado', methods=['POST'])
@login_required
def registrar_cobro_avanzado():
    cxc_id = request.form.get('cxc_id', type=int)
    moneda_cobro = request.form.get('moneda_cobro', 'PYG')
    cotizacion = float(request.form.get('cotizacion') or 1)
    monto_recibido = float(request.form.get('monto_recibido') or 0)
    descuento = float(request.form.get('descuento') or 0)
    motivo_descuento = request.form.get('motivo_descuento', '')
    forma_cobro = request.form.get('forma_cobro', 'Efectivo')
    cuenta_id = request.form.get('cuenta_id', type=int)

    cxc = CuentaPorCobrar.query.get_or_404(cxc_id)
    equivalente_gs = monto_recibido * cotizacion

    if equivalente_gs > 0 and cuenta_id:
        db.session.add(Transaccion(
            descripcion=f"Cobro avanzado: {cxc.concepto}",
            tipo='Ingreso',
            monto=equivalente_gs,
            cuenta_id=cuenta_id,
            cliente_id=cxc.cliente_id,
            metodo_pago=forma_cobro,
            conciliado=False,
            fecha=datetime.utcnow(),
            observacion=f"Moneda: {moneda_cobro}, Monto original: {monto_recibido}, Cotización: {cotizacion}, Descuento: {descuento} ({motivo_descuento})"
        ))
        cxc.estado = 'Pagado'
        db.session.commit()
        flash('Cobro registrado correctamente con conversión multimoneda.', 'success')
    
    return redirect(url_for('main.cxc_vista'))

@bp.route('/transferencia', methods=['POST'])
@login_required
def transferencia():
    cuenta_origen_id = int(request.form.get('cuenta_origen_id'))
    cuenta_destino_id = int(request.form.get('cuenta_destino_id'))
    monto = float(request.form.get('monto') or 0)
    
    if cuenta_origen_id != cuenta_destino_id and monto > 0:
        orig = Cuenta.query.get(cuenta_origen_id)
        dest = Cuenta.query.get(cuenta_destino_id)
        if orig and dest:
            db.session.add(Transaccion(
                descripcion=f"Transferencia saliente a {dest.nombre}",
                tipo='Salida',
                monto=monto,
                cuenta_id=cuenta_origen_id,
                metodo_pago='Transferencia',
                fecha=datetime.utcnow()
            ))
            db.session.add(Transaccion(
                descripcion=f"Transferencia entrante de {orig.nombre}",
                tipo='Ingreso',
                monto=monto,
                cuenta_id=cuenta_destino_id,
                metodo_pago='Transferencia',
                fecha=datetime.utcnow()
            ))
            db.session.commit()
    return redirect(url_for('main.dashboard'))

@bp.route('/cadastros')
@login_required
def cadastros():
    cuentas = Cuenta.query.all()
    clientes = Cliente.query.all()
    medicos = Medico.query.all()
    servicios = Servicio.query.all()
    paquetes = Paquete.query.all()
    cxc_list = CuentaPorCobrar.query.all()
    cxp_list = CuentaPorPagar.query.all()
    categorias = Categoria.query.all()
    centros_costo = CentroCosto.query.all()
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

@bp.route('/agregar-paquete', methods=['POST'])
@login_required
def agregar_paquete():
    nombre = request.form.get('nombre')
    moneda = request.form.get('moneda', 'PYG')
    precio = float(request.form.get('precio') or 0)
    duracion_meses = int(request.form.get('duracion_meses') or 4)
    
    paquete = Paquete(nombre=nombre, moneda=moneda, precio=precio, duracion_meses=duracion_meses)
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
                costo_unitario=float(costos_unitarios[i]) if i < len(costos_unitarios) and costos_unitarios[i] else 0.0,
                reparto_adicional=float(repartos[i]) if i < len(repartos) and repartos[i] else 0.0
            ))
            
    db.session.commit()
    flash('Plan o paquete creado exitosamente.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/eliminar-paquete/<int:id>', methods=['POST'])
@login_required
def eliminar_paquete(id):
    p = Paquete.query.get_or_404(id)
    db.session.delete(p)
    db.session.commit()
    flash('Plan o paquete eliminado correctamente.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/cuentas-por-cobrar')
@login_required
def cxc_vista():
    cxc_list = CuentaPorCobrar.query.all()
    clientes = Cliente.query.all()
    cuentas = Cuenta.query.all()
    return render_template('cxc.html', cxc_list=cxc_list, clientes=clientes, cuentas=cuentas)

@bp.route('/cuentas-a-pagar')
@login_required
def cxp_vista():
    cxp_list = CuentaPorPagar.query.all()
    cuentas = Cuenta.query.all()
    categorias = Categoria.query.filter_by(tipo='Salida').all()
    return render_template('cxp.html', cxp_list=cxp_list, cuentas=cuentas, categorias=categorias)

@bp.route('/honorarios')
@login_required
def honorarios():
    medicos = Medico.query.all()
    servicios = Servicio.query.all()
    clientes = Cliente.query.all()
    prestaciones = PrestacionServicio.query.order_by(PrestacionServicio.fecha.desc()).all()
    return render_template('honorarios.html', medicos=medicos, servicios=servicios, clientes=clientes, prestaciones=prestaciones)

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
                metodo_pago='Transferencia',
                monto=honorario_medico,
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
    cxc_pendientes = CuentaPorCobrar.query.filter_by(estado='Pendiente').all()
    cxp_pendientes = CuentaPorPagar.query.filter_by(estado='Pendiente').all()
    total_cxc = sum(x.monto for x in cxc_pendientes)
    total_cxp = sum(p.monto for p in cxp_pendientes)
    
    # Simulación de distribución temporal del costo de servicios (25% por mes durante 4 meses)
    ventas_activas = Venta.query.filter(Venta.estado != 'Cancelada').all()
    distribucion_costos = sum((v.costo_servicios * 0.25) for v in ventas_activas)
    
    saldo_proyectado = total_cxc - (total_cxp + distribucion_costos)
    
    return render_template('flujo_proyectado.html', 
                           cxc_pendientes=cxc_pendientes, 
                           cxp_pendientes=cxp_pendientes, 
                           total_cxc=total_cxc, 
                           total_cxp=total_cxp,
                           distribucion_costos=distribucion_costos,
                           saldo_proyectado=saldo_proyectado)

@bp.route('/agregar-transaccion', methods=['POST'])
@login_required
def agregar_transaccion():
    cat_id = request.form.get('categoria_id')
    cta_id = request.form.get('cuenta_id')
    cc_id = request.form.get('centro_costo_id')
    srv_id = request.form.get('servicio_id')
    cliente_id = int(request.form.get('cliente_id')) if request.form.get('cliente_id') else None

    descripcion = request.form.get('descripcion')
    tipo = request.form.get('tipo')
    monto_ingresado = float(request.form.get('monto') or 0)
    metodo_pago = request.form.get('metodo_pago', 'Efectivo')
    fecha_transaccion = datetime.strptime(request.form.get('fecha'), '%Y-%m-%d') if request.form.get('fecha') else datetime.utcnow()
    observacion = request.form.get('observacion', '')

    if monto_ingresado > 0:
        db.session.add(Transaccion(
            descripcion=descripcion,
            tipo=tipo,
            monto=monto_ingresado,
            categoria_id=int(cat_id) if cat_id else None,
            cuenta_id=int(cta_id) if cta_id else None,
            centro_costo_id=int(cc_id) if cc_id else None,
            servicio_id=int(srv_id) if srv_id else None,
            cliente_id=cliente_id,
            metodo_pago=metodo_pago,
            conciliado=False,
            fecha=fecha_transaccion,
            observacion=observacion
        ))
        db.session.commit()
        flash('Transacción agregada con éxito.', 'success')
    return redirect(url_for('main.dashboard'))

@bp.route('/editar-transaccion/<int:id>', methods=['POST'])
@login_required
def editar_transaccion(id):
    t = Transaccion.query.get_or_404(id)
    t.tipo = request.form.get('tipo')
    t.descripcion = request.form.get('descripcion')
    t.monto = float(request.form.get('monto') or 0)
    t.metodo_pago = request.form.get('metodo_pago')
    t.cuenta_id = int(request.form.get('cuenta_id')) if request.form.get('cuenta_id') else None
    t.categoria_id = int(request.form.get('categoria_id')) if request.form.get('categoria_id') else None
    t.centro_costo_id = int(request.form.get('centro_costo_id')) if request.form.get('centro_costo_id') else None
    t.servicio_id = int(request.form.get('servicio_id')) if request.form.get('servicio_id') else None
    t.cliente_id = int(request.form.get('cliente_id')) if request.form.get('cliente_id') else None
    if request.form.get('fecha'):
        t.fecha = datetime.strptime(request.form.get('fecha'), '%Y-%m-%d')
    t.observacion = request.form.get('observacion', '')
    db.session.commit()
    flash('Transacción actualizada correctamente.', 'success')
    return redirect(url_for('main.dashboard'))

@bp.route('/eliminar-transaccion/<int:id>', methods=['POST'])
@login_required
def eliminar_transaccion(id):
    t = Transaccion.query.get_or_404(id)
    db.session.delete(t)
    db.session.commit()
    flash('Transacción eliminada correctamente.', 'success')
    return redirect(url_for('main.dashboard'))

@bp.route('/agregar-cuenta', methods=['POST'])
@login_required
def agregar_cuenta():
    nombre = request.form.get('nombre')
    tipo_cuenta = request.form.get('tipo_cuenta')
    moneda = request.form.get('moneda')
    saldo_inicial = float(request.form.get('saldo_inicial') or 0)
    color = request.form.get('color', '#17b978')
    db.session.add(Cuenta(nombre=nombre, tipo_cuenta=tipo_cuenta, moneda=moneda, saldo_inicial=saldo_inicial, color=color))
    db.session.commit()
    flash('Cuenta creada con éxito.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/eliminar-cuenta/<int:id>', methods=['POST'])
@login_required
def eliminar_cuenta(id):
    c = Cuenta.query.get_or_404(id)
    db.session.delete(c)
    db.session.commit()
    flash('Cuenta eliminada correctamente.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/agregar-cliente', methods=['POST'])
@login_required
def agregar_cliente():
    nombre = request.form.get('nombre')
    documento = request.form.get('documento')
    telefono = request.form.get('telefono')
    email = request.form.get('email')
    db.session.add(Cliente(nombre=nombre, documento=documento, telefono=telefono, email=email))
    db.session.commit()
    flash('Cliente registrado exitosamente.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/agregar-cliente-ajax', methods=['POST'])
@login_required
def agregar_cliente_ajax():
    nombre = request.form.get('nombre')
    telefono = request.form.get('telefono')
    if nombre:
        cl = Cliente(nombre=nombre, telefono=telefono)
        db.session.add(cl)
        db.session.commit()
        return {'success': True, 'id': cl.id, 'nombre': cl.nombre}
    return {'success': False}, 400

@bp.route('/eliminar-cliente/<int:id>', methods=['POST'])
@login_required
def eliminar_cliente(id):
    cl = Cliente.query.get_or_404(id)
    db.session.delete(cl)
    db.session.commit()
    flash('Cliente eliminado correctamente.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/agregar-medico', methods=['POST'])
@login_required
def agregar_medico():
    nombre = request.form.get('nombre')
    especialidad = request.form.get('especialidad')
    porcentaje_medico = float(request.form.get('porcentaje_medico') or 70)
    porcentaje_clinica = float(request.form.get('porcentaje_clinica') or 30)
    db.session.add(Medico(nombre=nombre, especialidad=especialidad, porcentaje_medico=porcentaje_medico, porcentaje_clinica=porcentaje_clinica))
    db.session.commit()
    flash('Profesional médico agregado con éxito.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/eliminar-medico/<int:id>', methods=['POST'])
@login_required
def eliminar_medico(id):
    m = Medico.query.get_or_404(id)
    db.session.delete(m)
    db.session.commit()
    flash('Profesional eliminado correctamente.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/agregar-servicio', methods=['POST'])
@login_required
def agregar_servicio():
    codigo = request.form.get('codigo')
    nombre = request.form.get('nombre')
    descripcion = request.form.get('descripcion')
    moneda = request.form.get('moneda')
    precio = float(request.form.get('precio') or 0)
    costo_unitario = float(request.form.get('costo_unitario') or 0)
    profesional_asociado = request.form.get('profesional_asociado')
    reparto_adicional = float(request.form.get('reparto_adicional') or 0)
    
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

@bp.route('/eliminar-servicio/<int:id>', methods=['POST'])
@login_required
def eliminar_servicio(id):
    s = Servicio.query.get_or_404(id)
    db.session.delete(s)
    db.session.commit()
    flash('Servicio eliminado correctamente.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/agregar-cxc', methods=['POST'])
@login_required
def agregar_cxc():
    cliente_id = int(request.form.get('cliente_id')) if request.form.get('cliente_id') else None
    concepto = request.form.get('concepto')
    moneda = request.form.get('moneda')
    metodo_pago = request.form.get('metodo_pago', 'Efectivo')
    monto = float(request.form.get('monto') or 0)
    fecha_vencimiento = datetime.strptime(request.form.get('fecha_vencimiento'), '%Y-%m-%d') if request.form.get('fecha_vencimiento') else datetime.utcnow()
    
    db.session.add(CuentaPorCobrar(
        cliente_id=cliente_id,
        concepto=concepto,
        moneda=moneda,
        metodo_pago=metodo_pago,
        monto=monto,
        fecha_emision=datetime.utcnow(),
        fecha_vencimiento=fecha_vencimiento,
        estado='Pendiente'
    ))
    db.session.commit()
    flash('Cuenta por cobrar agregada con éxito.', 'success')
    return redirect(url_for('main.cxc_vista'))

@bp.route('/eliminar-cxc/<int:id>', methods=['POST'])
@login_required
def eliminar_cxc(id):
    x = CuentaPorCobrar.query.get_or_404(id)
    db.session.delete(x)
    db.session.commit()
    flash('Cuenta por cobrar eliminada correctamente.', 'success')
    return redirect(url_for('main.cxc_vista'))

@bp.route('/cobrar-cxc/<int:id>', methods=['POST'])
@login_required
def cobrar_cxc(id):
    x = CuentaPorCobrar.query.get_or_404(id)
    cuenta_id = int(request.form.get('cuenta_id'))
    x.estado = 'Pagado'
    
    db.session.add(Transaccion(
        descripcion=f"Cobro CxC: {x.concepto}",
        tipo='Ingreso',
        monto=x.monto,
        cuenta_id=cuenta_id,
        cliente_id=x.cliente_id,
        metodo_pago=x.metodo_pago,
        conciliado=False,
        fecha=datetime.utcnow(),
        observacion="Cobro de cuenta por cobrar"
    ))
    db.session.commit()
    flash('Cobro registrado y transacción generada con éxito.', 'success')
    return redirect(url_for('main.cxc_vista'))

@bp.route('/agregar-cxp', methods=['POST'])
@login_required
def agregar_cxp():
    proveedor = request.form.get('proveedor')
    concepto = request.form.get('concepto')
    moneda = request.form.get('moneda')
    metodo_pago = request.form.get('metodo_pago', 'Efectivo')
    monto = float(request.form.get('monto') or 0)
    categoria_id = int(request.form.get('categoria_id')) if request.form.get('categoria_id') else None
    fecha_vencimiento = datetime.strptime(request.form.get('fecha_vencimiento'), '%Y-%m-%d') if request.form.get('fecha_vencimiento') else datetime.utcnow()
    
    db.session.add(CuentaPorPagar(
        proveedor=proveedor,
        concepto=concepto,
        moneda=moneda,
        metodo_pago=metodo_pago,
        monto=monto,
        categoria_id=categoria_id,
        fecha_factura=datetime.utcnow(),
        fecha_vencimiento=fecha_vencimiento,
        estado='Pendiente'
    ))
    db.session.commit()
    flash('Cuenta a pagar agregada con éxito.', 'success')
    return redirect(url_for('main.cxp_vista'))

@bp.route('/eliminar-cxp/<int:id>', methods=['POST'])
@login_required
def eliminar_cxp(id):
    p = CuentaPorPagar.query.get_or_404(id)
    db.session.delete(p)
    db.session.commit()
    flash('Cuenta a pagar eliminada correctamente.', 'success')
    return redirect(url_for('main.cxp_vista'))

@bp.route('/pagar-cxp/<int:id>', methods=['POST'])
@login_required
def pagar_cxp(id):
    p = CuentaPorPagar.query.get_or_404(id)
    cuenta_id = int(request.form.get('cuenta_id'))
    p.estado = 'Pagado'
    
    db.session.add(Transaccion(
        descripcion=f"Pago CxP: {p.concepto} - {p.proveedor}",
        tipo='Salida',
        monto=p.monto,
        cuenta_id=cuenta_id,
        categoria_id=p.categoria_id,
        metodo_pago=p.metodo_pago,
        conciliado=False,
        fecha=datetime.utcnow(),
        observacion="Pago de cuenta a pagar"
    ))
    db.session.commit()
    flash('Pago registrado con éxito.', 'success')
    return redirect(url_for('main.cxp_vista'))

@bp.route('/agregar-categoria', methods=['POST'])
@login_required
def agregar_categoria():
    nombre = request.form.get('nombre')
    tipo = request.form.get('tipo')
    db.session.add(Categoria(nombre=nombre, tipo=tipo))
    db.session.commit()
    flash('Categoría creada correctamente.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/agregar-centro-costo', methods=['POST'])
@login_required
def agregar_centro_costo():
    nombre = request.form.get('nombre')
    db.session.add(CentroCosto(nombre=nombre))
    db.session.commit()
    flash('Centro de costo agregado con éxito.', 'success')
    return redirect(url_for('main.cadastros'))

@bp.route('/exportar-excel')
@login_required
def exportar_excel():
    transacciones = Transaccion.query.order_by(Transaccion.fecha.desc()).all()
    si = StringIO()
    cw = csv.writer(si, delimiter=';')
    cw.writerow(['Fecha', 'Descripción', 'Paciente/Cliente', 'Tipo', 'Categoría', 'Cuenta', 'Centro de Costo', 'Método de Pago', 'Monto'])
    for t in transacciones:
        cw.writerow([
            t.fecha.strftime('%d/%m/%Y') if t.fecha else '',
            t.descripcion,
            t.cliente.nombre if t.cliente else 'Particular',
            t.tipo,
            t.categoria.nombre if t.categoria else '',
            t.cuenta.nombre if t.cuenta else '',
            t.centro_costo.nombre if t.centro_costo else '',
            t.metodo_pago,
            t.monto
        ])
    output = si.getvalue().encode('utf-8-sig')
    return Response(output, mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=transacciones.csv"})

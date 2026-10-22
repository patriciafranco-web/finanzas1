from flask import Blueprint, render_template, request, redirect, url_for, Response
from app import db
from app.models import Cuenta, Transaccion, Cliente, Medico, Servicio, CuentaPorCobrar, CuentaPorPagar, Categoria, CentroCosto, PrestacionServicio
from datetime import datetime, time
import csv
from io import StringIO

bp = Blueprint('main', __name__)

@bp.route('/')
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

    return render_template('dashboard.html', 
                           transacciones=transacciones, 
                           cuentas=cuentas, 
                           categorias=categorias, 
                           centros_costo=centros_costo,
                           servicios=servicios,
                           clientes=clientes,
                           saldos=saldos,
                           selected_q=q,
                           selected_cuenta=cuenta_id,
                           selected_categoria=categoria_id,
                           selected_centro=centro_id,
                           selected_desde=fecha_desde_str,
                           selected_hasta=fecha_hasta_str)

@bp.route('/cadastros')
def cadastros():
    cuentas = Cuenta.query.all()
    clientes = Cliente.query.all()
    medicos = Medico.query.all()
    servicios = Servicio.query.all()
    cxc_list = CuentaPorCobrar.query.all()
    cxp_list = CuentaPorPagar.query.all()
    categorias = Categoria.query.all()
    centros_costo = CentroCosto.query.all()
    return render_template('cadastros.html', 
                           cuentas=cuentas, 
                           clientes=clientes, 
                           medicos=medicos, 
                           servicios=servicios,
                           cxc_list=cxc_list,
                           cxp_list=cxp_list,
                           categorias=categorias,
                           centros_costo=centros_costo)

@bp.route('/honorarios')
def honorarios():
    medicos = Medico.query.all()
    servicios = Servicio.query.all()
    clientes = Cliente.query.all()
    prestaciones = PrestacionServicio.query.order_by(PrestacionServicio.fecha.desc()).all()
    return render_template('honorarios.html', medicos=medicos, servicios=servicios, clientes=clientes, prestaciones=prestaciones)

@bp.route('/agregar-prestacion', methods=['POST'])
def agregar_prestacion():
    medico_id = int(request.form.get('medico_id')) if request.form.get('medico_id') else None
    servicio_id = int(request.form.get('servicio_id')) if request.form.get('servicio_id') else None
    cliente_id = int(request.form.get('cliente_id')) if request.form.get('cliente_id') else None
    
    if medico_id and servicio_id:
        medico = Medico.query.get(medico_id)
        servicio = Servicio.query.get(servicio_id)
        
        if medico and servicio:
            prestacion = PrestacionServicio(
                medico_id=medico_id,
                servicio_id=servicio_id,
                cliente_id=cliente_id,
                fecha=datetime.utcnow()
            )
            db.session.add(prestacion)
            
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
            
    return redirect(url_for('main.honorarios'))

@bp.route('/eliminar-prestacion/<int:id>', methods=['POST'])
def eliminar_prestacion(id):
    p = PrestacionServicio.query.get_or_404(id)
    db.session.delete(p)
    db.session.commit()
    return redirect(url_for('main.honorarios'))

@bp.route('/estado-resultados')
@bp.route('/pyg')
def pyg():
    transacciones = Transaccion.query.all()
    total_ingresos = sum(t.monto for t in transacciones if t.tipo == 'Ingreso')
    total_salidas = sum(t.monto for t in transacciones if t.tipo == 'Salida')
    utilidad_neta = total_ingresos - total_salidas
    return render_template('pyg.html', transacciones=transacciones, total_ingresos=total_ingresos, total_salidas=total_salidas, utilidad_neta=utilidad_neta)

@bp.route('/flujo-proyectado')
def flujo_proyectado():
    cxc_pendientes = CuentaPorCobrar.query.filter_by(estado='Pendiente').all()
    cxp_pendientes = CuentaPorPagar.query.filter_by(estado='Pendiente').all()
    total_cxc = sum(x.monto for x in cxc_pendientes)
    total_cxp = sum(p.monto for p in cxp_pendientes)
    return render_template('flujo_proyectado.html', cxc_pendientes=cxc_pendientes, cxp_pendientes=cxp_pendientes, total_cxc=total_cxc, total_cxp=total_cxp)

@bp.route('/agregar-transaccion', methods=['POST'])
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

    cuenta = Cuenta.query.get(cta_id) if cta_id else None
    usar_divisa = request.form.get('usar_divisa')

    if usar_divisa and cuenta:
        monto_divisa = float(request.form.get('monto_divisa') or 0)
        cotizacion = float(request.form.get('cotizacion') or 1)
        
        if cuenta.moneda == 'PYG':
            total_recibido_gs = monto_divisa * cotizacion
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
                    observacion=f"Pagado en divisa ({monto_divisa} USD) con cotiz: {cotizacion}. {observacion}"
                ))
            
            diferencia = total_recibido_gs - monto_ingresado
            if diferencia > 100:
                db.session.add(Transaccion(
                    descripcion=f"Ganancia por Cambio: {descripcion}",
                    tipo='Ingreso',
                    monto=diferencia,
                    categoria_id=int(cat_id) if cat_id else None,
                    cuenta_id=int(cta_id) if cta_id else None,
                    centro_costo_id=int(cc_id) if cc_id else None,
                    cliente_id=cliente_id,
                    metodo_pago='Ajuste de Cambio',
                    conciliado=False,
                    fecha=fecha_transaccion,
                    observacion="Sobrante generado por diferencia en la cotización del día"
                ))
        elif cuenta.moneda == 'USD':
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
                    observacion=f"Pagado en USD (Cotización aplicada: {cotizacion}). {observacion}"
                ))
            
            diferencia_usd = monto_divisa - monto_ingresado
            if diferencia_usd > 0.01:
                db.session.add(Transaccion(
                    descripcion=f"Ganancia por Cambio: {descripcion}",
                    tipo='Ingreso',
                    monto=diferencia_usd,
                    categoria_id=int(cat_id) if cat_id else None,
                    cuenta_id=int(cta_id) if cta_id else None,
                    centro_costo_id=int(cc_id) if cc_id else None,
                    cliente_id=cliente_id,
                    metodo_pago='Ajuste de Cambio',
                    conciliado=False,
                    fecha=fecha_transaccion,
                    observacion="Sobrante en USD por diferencia de cotización"
                ))
    else:
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
    return redirect(url_for('main.dashboard'))

@bp.route('/editar-transaccion/<int:id>', methods=['POST'])
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
    return redirect(url_for('main.dashboard'))

@bp.route('/eliminar-transaccion/<int:id>', methods=['POST'])
def eliminar_transaccion(id):
    t = Transaccion.query.get_or_404(id)
    db.session.delete(t)
    db.session.commit()
    return redirect(url_for('main.dashboard'))

@bp.route('/agregar-cuenta', methods=['POST'])
def agregar_cuenta():
    nombre = request.form.get('nombre')
    tipo_cuenta = request.form.get('tipo_cuenta')
    moneda = request.form.get('moneda')
    saldo_inicial = float(request.form.get('saldo_inicial') or 0)
    color = request.form.get('color', '#17b978')
    db.session.add(Cuenta(nombre=nombre, tipo_cuenta=tipo_cuenta, moneda=moneda, saldo_inicial=saldo_inicial, color=color))
    db.session.commit()
    return redirect(url_for('main.cadastros'))

@bp.route('/eliminar-cuenta/<int:id>', methods=['POST'])
def eliminar_cuenta(id):
    c = Cuenta.query.get_or_404(id)
    db.session.delete(c)
    db.session.commit()
    return redirect(url_for('main.cadastros'))

@bp.route('/agregar-cliente', methods=['POST'])
def agregar_cliente():
    nombre = request.form.get('nombre')
    documento = request.form.get('documento')
    telefono = request.form.get('telefono')
    email = request.form.get('email')
    db.session.add(Cliente(nombre=nombre, documento=documento, telefono=telefono, email=email))
    db.session.commit()
    return redirect(url_for('main.cadastros'))

@bp.route('/eliminar-cliente/<int:id>', methods=['POST'])
def eliminar_cliente(id):
    cl = Cliente.query.get_or_404(id)
    db.session.delete(cl)
    db.session.commit()
    return redirect(url_for('main.cadastros'))

@bp.route('/agregar-medico', methods=['POST'])
def agregar_medico():
    nombre = request.form.get('nombre')
    especialidad = request.form.get('especialidad')
    porcentaje_medico = float(request.form.get('porcentaje_medico') or 70)
    porcentaje_clinica = float(request.form.get('porcentaje_clinica') or 30)
    db.session.add(Medico(nombre=nombre, especialidad=especialidad, porcentaje_medico=porcentaje_medico, porcentaje_clinica=porcentaje_clinica))
    db.session.commit()
    return redirect(url_for('main.cadastros'))

@bp.route('/eliminar-medico/<int:id>', methods=['POST'])
def eliminar_medico(id):
    m = Medico.query.get_or_404(id)
    db.session.delete(m)
    db.session.commit()
    return redirect(url_for('main.cadastros'))

@bp.route('/agregar-servicio', methods=['POST'])
def agregar_servicio():
    nombre = request.form.get('nombre')
    moneda = request.form.get('moneda')
    precio = float(request.form.get('precio') or 0)
    db.session.add(Servicio(nombre=nombre, moneda=moneda, precio=precio))
    db.session.commit()
    return redirect(url_for('main.cadastros'))

@bp.route('/eliminar-servicio/<int:id>', methods=['POST'])
def eliminar_servicio(id):
    s = Servicio.query.get_or_404(id)
    db.session.delete(s)
    db.session.commit()
    return redirect(url_for('main.cadastros'))

@bp.route('/agregar-cxc', methods=['POST'])
def agregar_cxc():
    cliente_id = int(request.form.get('cliente_id')) if request.form.get('cliente_id') else None
    servicio_id = int(request.form.get('servicio_id')) if request.form.get('servicio_id') else None
    concepto = request.form.get('concepto')
    moneda = request.form.get('moneda')
    metodo_pago = request.form.get('metodo_pago', 'Efectivo')
    monto = float(request.form.get('monto') or 0)
    fecha_vencimiento = datetime.strptime(request.form.get('fecha_vencimiento'), '%Y-%m-%d') if request.form.get('fecha_vencimiento') else datetime.utcnow()
    
    db.session.add(CuentaPorCobrar(
        cliente_id=cliente_id,
        servicio_id=servicio_id,
        concepto=concepto,
        moneda=moneda,
        metodo_pago=metodo_pago,
        monto=monto,
        fecha_emision=datetime.utcnow(),
        fecha_vencimiento=fecha_vencimiento,
        estado='Pendiente'
    ))
    db.session.commit()
    return redirect(url_for('main.cadastros'))

@bp.route('/eliminar-cxc/<int:id>', methods=['POST'])
def eliminar_cxc(id):
    x = CuentaPorCobrar.query.get_or_404(id)
    db.session.delete(x)
    db.session.commit()
    return redirect(url_for('main.cadastros'))

@bp.route('/cobrar-cxc/<int:id>', methods=['POST'])
def cobrar_cxc(id):
    x = CuentaPorCobrar.query.get_or_404(id)
    cuenta_id = int(request.form.get('cuenta_id'))
    cuenta = Cuenta.query.get(cuenta_id)
    usar_divisa = request.form.get('usar_divisa')

    x.estado = 'Pagado'
    
    if usar_divisa and cuenta:
        monto_divisa = float(request.form.get('monto_divisa') or 0)
        cotizacion = float(request.form.get('cotizacion') or 1)
        total_recibido_gs = monto_divisa * cotizacion

        if cuenta.moneda == 'PYG':
            db.session.add(Transaccion(
                descripcion=f"Cobro CxC: {x.concepto}",
                tipo='Ingreso',
                monto=x.monto,
                cuenta_id=cuenta_id,
                cliente_id=x.cliente_id,
                metodo_pago=x.metodo_pago,
                conciliado=False,
                fecha=datetime.utcnow(),
                observacion=f"Cobro CxC en divisa ({monto_divisa} USD) cotiz: {cotizacion}"
            ))
            diferencia = total_recibido_gs - x.monto
            if diferencia > 100:
                db.session.add(Transaccion(
                    descripcion=f"Ganancia por Cambio: {x.concepto}",
                    tipo='Ingreso',
                    monto=diferencia,
                    cuenta_id=cuenta_id,
                    cliente_id=x.cliente_id,
                    metodo_pago='Ajuste de Cambio',
                    conciliado=False,
                    fecha=datetime.utcnow(),
                    observacion="Sobrante generado por diferencia en la cotización al cobrar CxC"
                ))
        else:
            db.session.add(Transaccion(
                descripcion=f"Cobro CxC: {x.concepto}",
                tipo='Ingreso',
                monto=x.monto,
                cuenta_id=cuenta_id,
                cliente_id=x.cliente_id,
                metodo_pago=x.metodo_pago,
                conciliado=False,
                fecha=datetime.utcnow(),
                observacion="Cobro CxC en USD"
            ))
    else:
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
    return redirect(url_for('main.cadastros'))

@bp.route('/agregar-cxp', methods=['POST'])
def agregar_cxp():
    proveedor = request.form.get('proveedor')
    concepto = request.form.get('concepto')
    moneda = request.form.get('moneda')
    metodo_pago = request.form.get('metodo_pago', 'Efectivo')
    monto = float(request.form.get('monto') or 0)
    fecha_vencimiento = datetime.strptime(request.form.get('fecha_vencimiento'), '%Y-%m-%d') if request.form.get('fecha_vencimiento') else datetime.utcnow()
    
    db.session.add(CuentaPorPagar(
        proveedor=proveedor,
        concepto=concepto,
        moneda=moneda,
        metodo_pago=metodo_pago,
        monto=monto,
        fecha_factura=datetime.utcnow(),
        fecha_vencimiento=fecha_vencimiento,
        estado='Pendiente'
    ))
    db.session.commit()
    return redirect(url_for('main.cadastros'))

@bp.route('/eliminar-cxp/<int:id>', methods=['POST'])
def eliminar_cxp(id):
    p = CuentaPorPagar.query.get_or_404(id)
    db.session.delete(p)
    db.session.commit()
    return redirect(url_for('main.cadastros'))

@bp.route('/pagar-cxp/<int:id>', methods=['POST'])
def pagar_cxp(id):
    p = CuentaPorPagar.query.get_or_404(id)
    cuenta_id = int(request.form.get('cuenta_id'))
    p.estado = 'Pagado'
    
    db.session.add(Transaccion(
        descripcion=f"Pago CxP: {p.concepto} - {p.proveedor}",
        tipo='Salida',
        monto=p.monto,
        cuenta_id=cuenta_id,
        metodo_pago=p.metodo_pago,
        conciliado=False,
        fecha=datetime.utcnow(),
        observacion="Pago de cuenta a pagar"
    ))
    db.session.commit()
    return redirect(url_for('main.cadastros'))

@bp.route('/agregar-categoria', methods=['POST'])
def agregar_categoria():
    nombre = request.form.get('nombre')
    tipo = request.form.get('tipo')
    db.session.add(Categoria(nombre=nombre, tipo=tipo))
    db.session.commit()
    return redirect(url_for('main.cadastros'))

@bp.route('/agregar-centro-costo', methods=['POST'])
def agregar_centro_costo():
    nombre = request.form.get('nombre')
    db.session.add(CentroCosto(nombre=nombre))
    db.session.commit()
    return redirect(url_for('main.cadastros'))

@bp.route('/exportar-excel')
def exportar_excel():
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

    transacciones = query.order_by(Transaccion.fecha.desc()).all()
    
    si = StringIO()
    cw = csv.writer(si, delimiter=';')
    cw.writerow(['Fecha', 'Descripción', 'Paciente/Cliente', 'Tipo', 'Categoría', 'Cuenta', 'Centro de Costo', 'Método de Pago', 'Monto', 'Conciliado'])
    
    for t in transacciones:
        cw.writerow([
            t.fecha.strftime('%d/%m/%Y') if t.fecha else '',
            t.descripcion,
            t.cliente.nombre if t.cliente else 'Particular / General',
            t.tipo,
            t.categoria.nombre if t.categoria else '',
            t.cuenta.nombre if t.cuenta else '',
            t.centro_costo.nombre if t.centro_costo else '',
            t.metodo_pago,
            t.monto,
            'Sí' if t.conciliado else 'No'
        ])
        
    output = si.getvalue().encode('utf-8-sig')
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=transacciones_filtradas.csv"}
    )

@bp.route('/conciliacion')
def conciliacion():
    cuentas = Cuenta.query.all()
    transacciones = Transaccion.query.filter_by(conciliado=False).all()
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
    return render_template('conciliacion.html', cuentas=cuentas, transacciones=transacciones, saldos=saldos)

@bp.route('/auto-conciliar', methods=['POST', 'GET'])
def auto_conciliar():
    transacciones = Transaccion.query.filter_by(conciliado=False).all()
    for t in transacciones:
        t.conciliado = True
    db.session.commit()
    return redirect(url_for('main.conciliacion'))
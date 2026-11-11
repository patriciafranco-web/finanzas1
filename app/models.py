from app import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

class User(db.Model, UserMixin):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    rol = db.Column(db.String(50), default='Gerente Financiera')
    
    @property
    def role(self):
        return 'recepcion' if self.username.lower() == 'recepcion' else 'admin'
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Cuenta(db.Model):
    __tablename__ = 'cuenta'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    tipo_cuenta = db.Column(db.String(50), nullable=False)
    moneda = db.Column(db.String(10), nullable=False)
    saldo_inicial = db.Column(db.Float, default=0.0)
    color = db.Column(db.String(20), default='#17b978')
    activo = db.Column(db.Boolean, default=True)

class Cliente(db.Model):
    __tablename__ = 'cliente'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    documento = db.Column(db.String(50))
    telefono = db.Column(db.String(50))
    email = db.Column(db.String(100))
    fecha_nacimiento = db.Column(db.Date, nullable=True)
    activo = db.Column(db.Boolean, default=True)

class Medico(db.Model):
    __tablename__ = 'medico'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    especialidad = db.Column(db.String(100))
    porcentaje_medico = db.Column(db.Float, default=70.0)
    porcentaje_clinica = db.Column(db.Float, default=30.0)
    activo = db.Column(db.Boolean, default=True)

class Servicio(db.Model):
    __tablename__ = 'servicio'
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(50), unique=True, nullable=True)
    nombre = db.Column(db.String(150), nullable=False)
    descripcion = db.Column(db.Text)
    moneda = db.Column(db.String(10), nullable=False)
    precio = db.Column(db.Float, nullable=False)
    activo = db.Column(db.Boolean, default=True)
    costo_unitario = db.Column(db.Float, default=0.0)
    profesional_asociado = db.Column(db.String(100), nullable=True)
    reparto_adicional = db.Column(db.Float, default=0.0)

class Categoria(db.Model):
    __tablename__ = 'categoria'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)
    activo = db.Column(db.Boolean, default=True)

class CentroCosto(db.Model):
    __tablename__ = 'centro_costo'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    activo = db.Column(db.Boolean, default=True)

class Transaccion(db.Model):
    __tablename__ = 'transaccion'
    id = db.Column(db.Integer, primary_key=True)
    descripcion = db.Column(db.String(200), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)
    moneda = db.Column(db.String(10), default='PYG')
    monto = db.Column(db.Float, nullable=False)
    cotizacion = db.Column(db.Float, default=1.0)
    equivalente_gs = db.Column(db.Float, default=0.0)
    descuento_aplicado = db.Column(db.Float, default=0.0)
    metodo_pago = db.Column(db.String(50), default='Efectivo')
    conciliado = db.Column(db.Boolean, default=False)
    fecha = db.Column(db.DateTime, nullable=False)
    observacion = db.Column(db.Text)
    usuario = db.Column(db.String(100), default='admin')
    
    cuenta_id = db.Column(db.Integer, db.ForeignKey('cuenta.id'), nullable=True)
    cuenta = db.relationship('Cuenta', backref='transacciones')
    categoria_id = db.Column(db.Integer, db.ForeignKey('categoria.id'), nullable=True)
    categoria = db.relationship('Categoria', backref='transacciones')
    centro_costo_id = db.Column(db.Integer, db.ForeignKey('centro_costo.id'), nullable=True)
    centro_costo = db.relationship('CentroCosto', backref='transacciones')
    servicio_id = db.Column(db.Integer, db.ForeignKey('servicio.id'), nullable=True)
    servicio = db.relationship('Servicio', backref='transacciones')
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=True)
    cliente = db.relationship('Cliente', backref='transacciones')
    
    # Vínculos con títulos (CxC / CxP / Ventas / Transferencias)
    cxc_id = db.Column(db.Integer, db.ForeignKey('cuenta_por_cobrar.id'), nullable=True)
    venta_id = db.Column(db.Integer, db.ForeignKey('venta.id'), nullable=True)
    cxp_id = db.Column(db.Integer, db.ForeignKey('cuenta_por_pagar.id'), nullable=True)
    transferencia_par_id = db.Column(db.Integer, nullable=True)
    es_devolucion = db.Column(db.Boolean, default=False)

class CuentaPorCobrar(db.Model):
    __tablename__ = 'cuenta_por_cobrar'
    id = db.Column(db.Integer, primary_key=True)
    concepto = db.Column(db.String(200), nullable=False)
    moneda = db.Column(db.String(10), nullable=False, default='PYG')
    metodo_pago = db.Column(db.String(50), default='-')
    monto = db.Column(db.Float, nullable=False) # Total de la cuota
    pagado = db.Column(db.Float, default=0.0)
    descuento = db.Column(db.Float, default=0.0)
    saldo = db.Column(db.Float, default=0.0)
    fecha_emision = db.Column(db.DateTime, nullable=False)
    fecha_vencimiento = db.Column(db.DateTime, nullable=False)
    estado = db.Column(db.String(20), default='Pendiente') # Pendiente, Parcial, Pagado, Cancelado
    motivo_cancelacion = db.Column(db.Text, nullable=True)
    numero_cuota_str = db.Column(db.String(20), nullable=True)
    
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=True)
    cliente = db.relationship('Cliente', backref='cxc')
    venta_id = db.Column(db.Integer, db.ForeignKey('venta.id'), nullable=True)
    venta = db.relationship('Venta', backref='cuotas_cxc')
    cuenta_id = db.Column(db.Integer, db.ForeignKey('cuenta.id'), nullable=True)
    cuenta = db.relationship('Cuenta', foreign_keys=[cuenta_id])
    cuenta_nombre = db.Column(db.String(100), default='-')
    pagos = db.relationship('Transaccion', backref='cxc_titulo', foreign_keys='Transaccion.cxc_id', lazy=True)

class CuentaPorPagar(db.Model):
    __tablename__ = 'cuenta_por_pagar'
    id = db.Column(db.Integer, primary_key=True)
    proveedor = db.Column(db.String(150), nullable=False)
    concepto = db.Column(db.String(200), nullable=False)
    moneda = db.Column(db.String(10), nullable=False, default='PYG')
    metodo_pago = db.Column(db.String(50), default='-')
    monto = db.Column(db.Float, nullable=False) # Total del título
    pagado = db.Column(db.Float, default=0.0)
    saldo = db.Column(db.Float, default=0.0)
    fecha_factura = db.Column(db.DateTime, nullable=False)
    fecha_vencimiento = db.Column(db.DateTime, nullable=False)
    estado = db.Column(db.String(20), default='Pendiente') # Pendiente, Parcial, Pagado, Cancelado
    motivo_cancelacion = db.Column(db.Text, nullable=True)
    
    categoria_id = db.Column(db.Integer, db.ForeignKey('categoria.id'), nullable=True)
    categoria = db.relationship('Categoria', backref='cxp')
    cuenta_id = db.Column(db.Integer, db.ForeignKey('cuenta.id'), nullable=True)
    cuenta = db.relationship('Cuenta', foreign_keys=[cuenta_id])
    cuenta_nombre = db.Column(db.String(100), default='-')
    pagos = db.relationship('Transaccion', backref='cxp_titulo', foreign_keys='Transaccion.cxp_id', lazy=True)

class PrestacionServicio(db.Model):
    __tablename__ = 'prestacion_servicio'
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, nullable=False)
    cantidad = db.Column(db.Integer, default=1)
    medico_id = db.Column(db.Integer, db.ForeignKey('medico.id'), nullable=False)
    medico = db.relationship('Medico', backref='prestaciones')
    servicio_id = db.Column(db.Integer, db.ForeignKey('servicio.id'), nullable=False)
    servicio = db.relationship('Servicio', backref='prestaciones')
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=True)
    cliente = db.relationship('Cliente', backref='prestaciones')

class Paquete(db.Model):
    __tablename__ = 'paquete'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    moneda = db.Column(db.String(10), default='PYG')
    precio = db.Column(db.Float, nullable=False)
    duracion_meses = db.Column(db.Integer, default=4)
    activo = db.Column(db.Boolean, default=True)
    items = db.relationship('PaqueteItem', backref='paquete', cascade='all, delete-orphan', lazy=True)

class PaqueteItem(db.Model):
    __tablename__ = 'paquete_item'
    id = db.Column(db.Integer, primary_key=True)
    paquete_id = db.Column(db.Integer, db.ForeignKey('paquete.id'), nullable=False)
    servicio_id = db.Column(db.Integer, db.ForeignKey('servicio.id'), nullable=False)
    servicio = db.relationship('Servicio')
    cantidad = db.Column(db.Integer, default=1)
    costo_unitario = db.Column(db.Float, default=0.0)
    reparto_adicional = db.Column(db.Float, default=0.0)
    
    @property
    def costo_total_item(self):
        return (self.cantidad or 1) * (self.costo_unitario or 0.0) * (1 + ((self.reparto_adicional or 0.0) / 100.0))

class Venta(db.Model):
    __tablename__ = 'venta'
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, nullable=False)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=True)
    cliente = db.relationship('Cliente', backref='ventas')
    paquete_id = db.Column(db.Integer, db.ForeignKey('paquete.id'), nullable=True)
    paquete = db.relationship('Paquete', backref='ventas')
    plan_nombre_historico = db.Column(db.String(200), nullable=True)
    forma_pago = db.Column(db.String(50), default='Cuotas (CxC)')
    num_cuotas = db.Column(db.Integer, default=1)
    cuenta_nombre = db.Column(db.String(100), default='-')
    
    subtotal = db.Column(db.Float, default=0.0)
    descuento = db.Column(db.Float, default=0.0)
    total = db.Column(db.Float, nullable=False)
    pagado = db.Column(db.Float, default=0.0)
    saldo = db.Column(db.Float, default=0.0)
    estado = db.Column(db.String(20), default='Pendiente') # Pendiente, Parcial, Pagado, Cancelada
    
    iva = db.Column(db.Float, default=0.0)
    tarifa_pasarela = db.Column(db.Float, default=0.0)
    costo_servicios = db.Column(db.Float, default=0.0)
    margen_contribucion = db.Column(db.Float, default=0.0)
    costo_fijo_plan = db.Column(db.Float, default=0.0)
    ganancia_plan = db.Column(db.Float, default=0.0)
    
    detalles = db.relationship('DetalleVenta', backref='venta', cascade='all, delete-orphan')
    cuotas = db.relationship('CuotaVenta', backref='venta', cascade='all, delete-orphan', lazy=True)

    @property
    def progreso_cuotas(self):
        if self.cuotas_cxc:
            activas = [c for c in self.cuotas_cxc if c.estado != 'Cancelado']
            total_c = len(activas) if activas else len(self.cuotas_cxc)
            pagadas_c = sum(1 for c in activas if c.estado == 'Pagado')
            return f"{pagadas_c}/{total_c}"
        if self.cuotas:
            total_c = len(self.cuotas)
            pagadas_c = sum(1 for c in self.cuotas if c.estado in ('Pagado', 'Pagada'))
            return f"{pagadas_c}/{total_c}"
        return f"{1 if self.estado in ('Pagado', 'Pagada') else 0}/{self.num_cuotas or 1}"

class DetalleVenta(db.Model):
    __tablename__ = 'detalle_venta'
    id = db.Column(db.Integer, primary_key=True)
    venta_id = db.Column(db.Integer, db.ForeignKey('venta.id'), nullable=False)
    servicio_id = db.Column(db.Integer, db.ForeignKey('servicio.id'), nullable=False)
    servicio = db.relationship('Servicio')
    servicio_nombre_historico = db.Column(db.String(150), nullable=True)
    cantidad = db.Column(db.Integer, default=1)
    precio_unitario = db.Column(db.Float, nullable=False)
    costo_unitario_historico = db.Column(db.Float, default=0.0)
    reparto_historico = db.Column(db.Float, default=0.0)
    subtotal = db.Column(db.Float, nullable=False)
    observacion = db.Column(db.String(200))

class CuotaVenta(db.Model):
    __tablename__ = 'cuota_venta'
    id = db.Column(db.Integer, primary_key=True)
    venta_id = db.Column(db.Integer, db.ForeignKey('venta.id'), nullable=False)
    numero_cuota = db.Column(db.String(20), nullable=False)
    vencimiento_label = db.Column(db.String(50), nullable=False)
    fecha_vencimiento = db.Column(db.DateTime, nullable=True)
    valor = db.Column(db.Float, nullable=False)
    estado = db.Column(db.String(20), default='Pendiente')

class BloqueoPeriodo(db.Model):
    __tablename__ = 'bloqueo_periodo'
    id = db.Column(db.Integer, primary_key=True)
    periodo = db.Column(db.String(50), nullable=False)
    fecha_desde = db.Column(db.Date, nullable=True)
    fecha_hasta = db.Column(db.Date, nullable=True)
    cuenta_id = db.Column(db.Integer, db.ForeignKey('cuenta.id'), nullable=True)
    cuenta = db.relationship('Cuenta', backref='bloqueos')
    fecha_bloqueo = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    usuario = db.Column(db.String(100), nullable=False)
    estado = db.Column(db.String(20), default='Bloqueado')
    motivo = db.Column(db.Text, nullable=True)
    motivo_desbloqueo = db.Column(db.Text, nullable=True)

class HistorialBloqueo(db.Model):
    __tablename__ = 'historial_bloqueo'
    id = db.Column(db.Integer, primary_key=True)
    fecha_hora = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    usuario = db.Column(db.String(100), nullable=False)
    accion = db.Column(db.String(50), nullable=False) # Bloqueo / Desbloqueo
    rango = db.Column(db.String(100), nullable=False)
    cuenta_nombre = db.Column(db.String(100), default='Todas las cuentas')
    motivo = db.Column(db.Text, nullable=True)

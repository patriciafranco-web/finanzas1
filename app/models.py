from app import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

class User(db.Model, UserMixin):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    rol = db.Column(db.String(50), default='Gerente Financiera')
    
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

class Cliente(db.Model):
    __tablename__ = 'cliente'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    documento = db.Column(db.String(50))
    telefono = db.Column(db.String(50))
    email = db.Column(db.String(100))

class Medico(db.Model):
    __tablename__ = 'medico'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    especialidad = db.Column(db.String(100))
    porcentaje_medico = db.Column(db.Float, default=70.0)
    porcentaje_clinica = db.Column(db.Float, default=30.0)

class Servicio(db.Model):
    __tablename__ = 'servicio'
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(50), unique=True, nullable=True)
    nombre = db.Column(db.String(150), nullable=False)
    descripcion = db.Column(db.Text)
    moneda = db.Column(db.String(10), nullable=False)
    precio = db.Column(db.Float, nullable=False)  # Precio de venta opcional o unitario
    activo = db.Column(db.Boolean, default=True)
    
    # Nuevos campos para el módulo avanzado de costos por servicio/paquete
    costo_unitario = db.Column(db.Float, default=0.0)
    profesional_asociado = db.Column(db.String(100), nullable=True)
    reparto_adicional = db.Column(db.Float, default=0.0)  # Porcentaje ej. 50% que se suma al costo

class Categoria(db.Model):
    __tablename__ = 'categoria'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)

class CentroCosto(db.Model):
    __tablename__ = 'centro_costo'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)

class Transaccion(db.Model):
    __tablename__ = 'transaccion'
    id = db.Column(db.Integer, primary_key=True)
    descripcion = db.Column(db.String(200), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)
    monto = db.Column(db.Float, nullable=False)
    metodo_pago = db.Column(db.String(50), default='Efectivo')
    conciliado = db.Column(db.Boolean, default=False)
    fecha = db.Column(db.DateTime, nullable=False)
    observacion = db.Column(db.Text)
    
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

class CuentaPorCobrar(db.Model):
    __tablename__ = 'cuenta_por_cobrar'
    id = db.Column(db.Integer, primary_key=True)
    concepto = db.Column(db.String(200), nullable=False)
    moneda = db.Column(db.String(10), nullable=False)
    metodo_pago = db.Column(db.String(50), default='Efectivo')
    monto = db.Column(db.Float, nullable=False)
    fecha_emision = db.Column(db.DateTime, nullable=False)
    fecha_vencimiento = db.Column(db.DateTime, nullable=False)
    estado = db.Column(db.String(20), default='Pendiente')
    
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=True)
    cliente = db.relationship('Cliente', backref='cxc')

class CuentaPorPagar(db.Model):
    __tablename__ = 'cuenta_por_pagar'
    id = db.Column(db.Integer, primary_key=True)
    proveedor = db.Column(db.String(150), nullable=False)
    concepto = db.Column(db.String(200), nullable=False)
    moneda = db.Column(db.String(10), nullable=False)
    metodo_pago = db.Column(db.String(50), default='Transferencia')
    monto = db.Column(db.Float, nullable=False)
    fecha_factura = db.Column(db.DateTime, nullable=False)
    fecha_vencimiento = db.Column(db.DateTime, nullable=False)
    estado = db.Column(db.String(20), default='Pendiente')
    
    categoria_id = db.Column(db.Integer, db.ForeignKey('categoria.id'), nullable=True)
    categoria = db.relationship('Categoria', backref='cxp')

class PrestacionServicio(db.Model):
    __tablename__ = 'prestacion_servicio'
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, nullable=False)
    
    medico_id = db.Column(db.Integer, db.ForeignKey('medico.id'), nullable=False)
    medico = db.relationship('Medico', backref='prestaciones')

    servicio_id = db.Column(db.Integer, db.ForeignKey('servicio.id'), nullable=False)
    servicio = db.relationship('Servicio', backref='prestaciones')

    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=True)
    cliente = db.relationship('Cliente', backref='prestaciones')

# --- NUEVOS MODELOS PARA PAQUETES / PLANES Y CÁLCULOS DE VENTAS ---

class Paquete(db.Model):
    __tablename__ = 'paquete'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    moneda = db.Column(db.String(10), default='PYG')
    precio = db.Column(db.Float, nullable=False)  # Precio total (IVA incluido)
    duracion_meses = db.Column(db.Integer, default=4)
    
    items = db.relationship('PaqueteItem', backref='paquete', cascade='all, delete-orphan', lazy=True)

class PaqueteItem(db.Model):
    __tablename__ = 'paquete_item'
    id = db.Column(db.Integer, primary_key=True)
    paquete_id = db.Column(db.Integer, db.ForeignKey('paquete.id'), nullable=False)
    servicio_id = db.Column(db.Integer, db.ForeignKey('servicio.id'), nullable=False)
    servicio = db.relationship('Servicio')
    
    cantidad = db.Column(db.Integer, default=1)
    costo_unitario = db.Column(db.Float, default=0.0)
    reparto_adicional = db.Column(db.Float, default=0.0)  # Porcentaje ej. 50%
    
    @property
    def costo_total_item(self):
        return self.cantidad * self.costo_unitario * (1 + (self.reparto_adicional / 100))

class Venta(db.Model):
    __tablename__ = 'venta'
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, nullable=False)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=True)
    cliente = db.relationship('Cliente', backref='ventas')
    
    paquete_id = db.Column(db.Integer, db.ForeignKey('paquete.id'), nullable=True)
    paquete = db.relationship('Paquete', backref='ventas')
    
    subtotal = db.Column(db.Float, default=0.0)
    descuento = db.Column(db.Float, default=0.0)
    total = db.Column(db.Float, nullable=False)
    pagado = db.Column(db.Float, default=0.0)
    saldo = db.Column(db.Float, default=0.0)
    estado = db.Column(db.String(20), default='Pendiente')  # Pagada, Parcial, Pendiente, Cancelada
    
    # Campos históricos calculados congelados al momento de la venta
    iva = db.Column(db.Float, default=0.0)
    tarifa_pasarela = db.Column(db.Float, default=0.0)
    costo_servicios = db.Column(db.Float, default=0.0)
    margen_contribucion = db.Column(db.Float, default=0.0)
    costo_fijo_plan = db.Column(db.Float, default=0.0)
    ganancia_plan = db.Column(db.Float, default=0.0)
    
    detalles = db.relationship('DetalleVenta', backref='venta', cascade='all, delete-orphan')
    cuotas = db.relationship('CuotaVenta', backref='venta', cascade='all, delete-orphan', lazy=True)

class DetalleVenta(db.Model):
    __tablename__ = 'detalle_venta'
    id = db.Column(db.Integer, primary_key=True)
    venta_id = db.Column(db.Integer, db.ForeignKey('venta.id'), nullable=False)
    servicio_id = db.Column(db.Integer, db.ForeignKey('servicio.id'), nullable=False)
    servicio = db.relationship('Servicio')
    cantidad = db.Column(db.Integer, default=1)
    precio_unitario = db.Column(db.Float, nullable=False)
    subtotal = db.Column(db.Float, nullable=False)
    observacion = db.Column(db.String(200))

class CuotaVenta(db.Model):
    __tablename__ = 'cuota_venta'
    id = db.Column(db.Integer, primary_key=True)
    venta_id = db.Column(db.Integer, db.ForeignKey('venta.id'), nullable=False)
    numero_cuota = db.Column(db.String(20), nullable=False)  # Ej: "1 de 4"
    vencimiento_label = db.Column(db.String(50), nullable=False)  # Ej: "Día de la venta", "1 mes después"
    valor = db.Column(db.Float, nullable=False)
    estado = db.Column(db.String(20), default='Pendiente')

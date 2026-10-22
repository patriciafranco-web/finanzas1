from app import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    rol = db.Column(db.String(50), default='Gerente Financiera')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Cuenta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    tipo_cuenta = db.Column(db.String(50), nullable=False) # Caja Efectivo, Cuenta Corriente
    moneda = db.Column(db.String(10), nullable=False) # PYG, USD, BRL
    saldo_inicial = db.Column(db.Float, default=0.0)
    color = db.Column(db.String(20), default='#17b978')

class Cliente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    documento = db.Column(db.String(50))
    telefono = db.Column(db.String(50))
    email = db.Column(db.String(100))

class Medico(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    especialidad = db.Column(db.String(100))
    porcentaje_medico = db.Column(db.Float, default=70.0)
    porcentaje_clinica = db.Column(db.Float, default=30.0)

class Servicio(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    moneda = db.Column(db.String(10), nullable=False)
    precio = db.Column(db.Float, nullable=False)

class Categoria(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    tipo = db.Column(db.String(20), nullable=False) # Ingreso, Salida

class CentroCosto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)

class Transaccion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    descripcion = db.Column(db.String(200), nullable=False)
    tipo = db.Column(db.String(20), nullable=False) # Ingreso, Salida
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
    id = db.Column(db.Integer, primary_key=True)
    concepto = db.Column(db.String(200), nullable=False)
    moneda = db.Column(db.String(10), nullable=False)
    metodo_pago = db.Column(db.String(50), default='Efectivo')
    monto = db.Column(db.Float, nullable=False)
    fecha_emision = db.Column(db.DateTime, nullable=False)
    fecha_vencimiento = db.Column(db.DateTime, nullable=False)
    estado = db.Column(db.String(20), default='Pendiente') # Pendiente, Pagado
    
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=True)
    cliente = db.relationship('Cliente', backref='cxc')

    servicio_id = db.Column(db.Integer, db.ForeignKey('servicio.id'), nullable=True)
    servicio = db.relationship('Servicio', backref='cxc')

class CuentaPorPagar(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    proveedor = db.Column(db.String(150), nullable=False)
    concepto = db.Column(db.String(200), nullable=False)
    moneda = db.Column(db.String(10), nullable=False)
    metodo_pago = db.Column(db.String(50), default='Transferencia')
    monto = db.Column(db.Float, nullable=False)
    fecha_factura = db.Column(db.DateTime, nullable=False)
    fecha_vencimiento = db.Column(db.DateTime, nullable=False)
    estado = db.Column(db.String(20), default='Pendiente') # Pendiente, Pagado

class PrestacionServicio(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, nullable=False)
    
    medico_id = db.Column(db.Integer, db.ForeignKey('medico.id'), nullable=False)
    medico = db.relationship('Medico', backref='prestaciones')

    servicio_id = db.Column(db.Integer, db.ForeignKey('servicio.id'), nullable=False)
    servicio = db.relationship('Servicio', backref='prestaciones')

    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=True)
    cliente = db.relationship('Cliente', backref='prestaciones')
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

db = SQLAlchemy()
login_manager = LoginManager()

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'clinica_finanzas_secret_key_2026'
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///clinica_finanzas.db'
    
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'main.login'

    # Importar TODOS los modelos para que SQLAlchemy los reconozca y cree sus tablas
    from app.models import User, Cuenta, Cliente, Medico, Servicio, Categoria, CentroCosto, Transaccion, CuentaPorCobrar, CuentaPorPagar, PrestacionServicio, Venta, DetalleVenta

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from app.routes import bp
    app.register_blueprint(bp)

    with app.app_context():
        # Recrea la base de datos limpia con todas las tablas nuevas
        db.drop_all()
        db.create_all()
        
        # Crear usuario administrador por defecto si no existe
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', rol='Gerente Financiera')
            admin.set_password('clinica2026')
            db.session.add(admin)
            
        # Crear categorías básicas si la tabla está vacía
        if not Categoria.query.first():
            db.session.add(Categoria(nombre='Consultas Médicas', tipo='Ingreso'))
            db.session.add(Categoria(nombre='Venta de Paquetes', tipo='Ingreso'))
            db.session.add(Categoria(nombre='Insumos y Farmacia', tipo='Salida'))
            db.session.add(Categoria(nombre='Honorarios Médicos', tipo='Salida'))

        # Crear un servicio de prueba si no existe
        if not Servicio.query.first():
            db.session.add(Servicio(codigo='SRV01', nombre='Consulta General', descripcion='Consulta médica general', moneda='PYG', precio=150000, activo=True))
            
        db.session.commit()

    return app

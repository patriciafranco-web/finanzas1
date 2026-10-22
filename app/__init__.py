import os
import sqlite3
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

    from app.models import User, Cuenta, Cliente, Medico, Servicio, Categoria, CentroCosto, Transaccion, CuentaPorCobrar, CuentaPorPagar, PrestacionServicio, Venta, DetalleVenta

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from app.routes import bp
    app.register_blueprint(bp)

    # Asegurar que la carpeta instance exista y verificar si la base de datos necesita actualización
    os.makedirs(app.instance_path, exist_ok=True)
    db_path = os.path.join(app.instance_path, 'clinica_finanzas.db')

    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(servicio);")
            columns = [col[1] for col in cursor.fetchall()]
            conn.close()
            # Si la tabla existe pero no tiene la columna 'activo', se limpia la BD para recrearla con la nueva estructura
            if columns and 'activo' not in columns:
                os.remove(db_path)
        except Exception:
            pass

    with app.app_context():
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

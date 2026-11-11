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
    app.config['PROPAGATE_EXCEPTIONS'] = True
    
    if os.path.exists('/var/data'):
        basedir = '/var/data'
    else:
        os.makedirs(app.instance_path, exist_ok=True)
        basedir = app.instance_path

    db_path = os.path.join(basedir, 'clinica_finanzas.db')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + db_path
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'main.login'

    from app.models import (
        User, Cuenta, Cliente, Medico, Servicio, Categoria, CentroCosto, 
        Transaccion, CuentaPorCobrar, CuentaPorPagar, PrestacionServicio, 
        Venta, DetalleVenta, Paquete, PaqueteItem, CuotaVenta, BloqueoPeriodo
    )

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from app.routes import bp
    app.register_blueprint(bp)

    with app.app_context():
        db.create_all()
        
        # Migración automática de columnas en el disco persistente /var/data
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            migraciones = [
                "ALTER TABLE cliente ADD COLUMN fecha_nacimiento DATE;",
                "ALTER TABLE cuenta ADD COLUMN color VARCHAR(20) DEFAULT '#17b978';",
                "ALTER TABLE cuenta ADD COLUMN saldo_inicial FLOAT DEFAULT 0.0;",
                "ALTER TABLE servicio ADD COLUMN costo_unitario FLOAT DEFAULT 0.0;",
                "ALTER TABLE servicio ADD COLUMN profesional_asociado VARCHAR(100);",
                "ALTER TABLE servicio ADD COLUMN reparto_adicional FLOAT DEFAULT 0.0;",
                "ALTER TABLE servicio ADD COLUMN activo BOOLEAN DEFAULT 1;",
                "ALTER TABLE transaccion ADD COLUMN observacion TEXT;",
                "ALTER TABLE transaccion ADD COLUMN conciliado BOOLEAN DEFAULT 0;"
            ]
            for sql in migraciones:
                try:
                    cursor.execute(sql)
                except Exception:
                    pass
            conn.commit()
            conn.close()

        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', rol='Gerente Financiera')
            admin.set_password('clinica2026')
            db.session.add(admin)

        if not User.query.filter_by(username='recepcion').first():
            recep = User(username='recepcion')
            recep.set_password('123456')
            db.session.add(recep)
            
        if not Categoria.query.first():
            db.session.add(Categoria(nombre='Consultas Médicas', tipo='Ingreso'))
            db.session.add(Categoria(nombre='Venta de Paquetes', tipo='Ingreso'))
            db.session.add(Categoria(nombre='Insumos y Farmacia', tipo='Salida'))
            db.session.add(Categoria(nombre='Honorarios Médicos', tipo='Salida'))

        if not Servicio.query.first():
            db.session.add(Servicio(codigo='SRV01', nombre='Consulta General', descripcion='Consulta médica general', moneda='PYG', precio=150000, costo_unitario=100000, activo=True))
            
        db.session.commit()

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)

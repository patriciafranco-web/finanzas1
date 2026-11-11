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
        Venta, DetalleVenta, Paquete, PaqueteItem, CuotaVenta, BloqueoPeriodo, HistorialBloqueo
    )

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from app.routes import bp
    app.register_blueprint(bp)

    with app.app_context():
        db.create_all()
        
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            migraciones = [
                "ALTER TABLE user ADD COLUMN rol VARCHAR(50) DEFAULT 'Gerente Financiera';",
                "ALTER TABLE cuenta ADD COLUMN color VARCHAR(20) DEFAULT '#17b978';",
                "ALTER TABLE cuenta ADD COLUMN saldo_inicial FLOAT DEFAULT 0.0;",
                "ALTER TABLE cuenta ADD COLUMN activo BOOLEAN DEFAULT 1;",
                "ALTER TABLE cliente ADD COLUMN fecha_nacimiento DATE;",
                "ALTER TABLE cliente ADD COLUMN activo BOOLEAN DEFAULT 1;",
                "ALTER TABLE medico ADD COLUMN porcentaje_clinica FLOAT DEFAULT 30.0;",
                "ALTER TABLE medico ADD COLUMN activo BOOLEAN DEFAULT 1;",
                "ALTER TABLE servicio ADD COLUMN costo_unitario FLOAT DEFAULT 0.0;",
                "ALTER TABLE servicio ADD COLUMN profesional_asociado VARCHAR(100);",
                "ALTER TABLE servicio ADD COLUMN reparto_adicional FLOAT DEFAULT 0.0;",
                "ALTER TABLE servicio ADD COLUMN activo BOOLEAN DEFAULT 1;",
                "ALTER TABLE categoria ADD COLUMN activo BOOLEAN DEFAULT 1;",
                "ALTER TABLE centro_costo ADD COLUMN activo BOOLEAN DEFAULT 1;",
                "ALTER TABLE paquete ADD COLUMN activo BOOLEAN DEFAULT 1;",
                "ALTER TABLE transaccion ADD COLUMN moneda VARCHAR(10) DEFAULT 'PYG';",
                "ALTER TABLE transaccion ADD COLUMN cotizacion FLOAT DEFAULT 1.0;",
                "ALTER TABLE transaccion ADD COLUMN equivalente_gs FLOAT DEFAULT 0.0;",
                "ALTER TABLE transaccion ADD COLUMN descuento_aplicado FLOAT DEFAULT 0.0;",
                "ALTER TABLE transaccion ADD COLUMN observacion TEXT;",
                "ALTER TABLE transaccion ADD COLUMN conciliado BOOLEAN DEFAULT 0;",
                "ALTER TABLE transaccion ADD COLUMN usuario VARCHAR(100) DEFAULT 'admin';",
                "ALTER TABLE transaccion ADD COLUMN cxc_id INTEGER;",
                "ALTER TABLE transaccion ADD COLUMN venta_id INTEGER;",
                "ALTER TABLE transaccion ADD COLUMN cxp_id INTEGER;",
                "ALTER TABLE transaccion ADD COLUMN transferencia_par_id INTEGER;",
                "ALTER TABLE transaccion ADD COLUMN es_devolucion BOOLEAN DEFAULT 0;",
                "ALTER TABLE cuenta_por_cobrar ADD COLUMN pagado FLOAT DEFAULT 0.0;",
                "ALTER TABLE cuenta_por_cobrar ADD COLUMN descuento FLOAT DEFAULT 0.0;",
                "ALTER TABLE cuenta_por_cobrar ADD COLUMN saldo FLOAT DEFAULT 0.0;",
                "ALTER TABLE cuenta_por_cobrar ADD COLUMN motivo_cancelacion TEXT;",
                "ALTER TABLE cuenta_por_cobrar ADD COLUMN numero_cuota_str VARCHAR(20);",
                "ALTER TABLE cuenta_por_cobrar ADD COLUMN venta_id INTEGER;",
                "ALTER TABLE cuenta_por_cobrar ADD COLUMN cuenta_id INTEGER;",
                "ALTER TABLE cuenta_por_cobrar ADD COLUMN cuenta_nombre VARCHAR(100) DEFAULT '-';",
                "ALTER TABLE cuenta_por_pagar ADD COLUMN pagado FLOAT DEFAULT 0.0;",
                "ALTER TABLE cuenta_por_pagar ADD COLUMN saldo FLOAT DEFAULT 0.0;",
                "ALTER TABLE cuenta_por_pagar ADD COLUMN motivo_cancelacion TEXT;",
                "ALTER TABLE cuenta_por_pagar ADD COLUMN cuenta_id INTEGER;",
                "ALTER TABLE cuenta_por_pagar ADD COLUMN cuenta_nombre VARCHAR(100) DEFAULT '-';",
                "ALTER TABLE prestacion_servicio ADD COLUMN cantidad INTEGER DEFAULT 1;",
                "ALTER TABLE venta ADD COLUMN plan_nombre_historico VARCHAR(200);",
                "ALTER TABLE venta ADD COLUMN forma_pago VARCHAR(50) DEFAULT 'Cuotas (CxC)';",
                "ALTER TABLE venta ADD COLUMN num_cuotas INTEGER DEFAULT 1;",
                "ALTER TABLE venta ADD COLUMN cuenta_nombre VARCHAR(100) DEFAULT '-';",
                "ALTER TABLE detalle_venta ADD COLUMN servicio_nombre_historico VARCHAR(150);",
                "ALTER TABLE detalle_venta ADD COLUMN costo_unitario_historico FLOAT DEFAULT 0.0;",
                "ALTER TABLE detalle_venta ADD COLUMN reparto_historico FLOAT DEFAULT 0.0;",
                "ALTER TABLE cuota_venta ADD COLUMN fecha_vencimiento DATETIME;",
                "ALTER TABLE bloqueo_periodo ADD COLUMN fecha_desde DATE;",
                "ALTER TABLE bloqueo_periodo ADD COLUMN fecha_hasta DATE;",
                "ALTER TABLE bloqueo_periodo ADD COLUMN motivo TEXT;"
            ]
            for sql in migraciones:
                try:
                    cursor.execute(sql)
                except Exception:
                    pass
            
            # Inicializar saldos en títulos antiguos donde saldo sea 0 y estado sea Pendiente
            try:
                cursor.execute("UPDATE cuenta_por_cobrar SET saldo = monto WHERE (saldo IS NULL OR saldo = 0) AND estado = 'Pendiente';")
                cursor.execute("UPDATE cuenta_por_cobrar SET pagado = monto, saldo = 0 WHERE estado = 'Pagado' AND (pagado IS NULL OR pagado = 0);")
                cursor.execute("UPDATE cuenta_por_pagar SET saldo = monto WHERE (saldo IS NULL OR saldo = 0) AND estado = 'Pendiente';")
                cursor.execute("UPDATE cuenta_por_pagar SET pagado = monto, saldo = 0 WHERE estado = 'Pagado' AND (pagado IS NULL OR pagado = 0);")
                # Cambiar cuentas Tarjeta Credito y Tarjeta Debito a tipo 'Tarjeta' (Pág. 23 del PDF)
                cursor.execute("UPDATE cuenta SET tipo_cuenta = 'Tarjeta' WHERE lower(nombre) LIKE '%tarjeta%';")
            except Exception:
                pass

            conn.commit()
            conn.close()

        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', rol='Gerente Financiera')
            admin.set_password('clinica2026')
            db.session.add(admin)

        if not User.query.filter_by(username='recepcion').first():
            recep = User(username='recepcion', rol='Recepcionista')
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

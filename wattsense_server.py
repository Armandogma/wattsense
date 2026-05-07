import json
from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta, time, timezone
import pandas as pd
from statsmodels.tsa.arima.model import ARIMA
import joblib
import os
from flask_socketio import SocketIO
import logging
from apscheduler.schedulers.background import BackgroundScheduler
import warnings
import numpy as np

# --- Configuración Inicial ---
warnings.filterwarnings("ignore", category=UserWarning)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///wattsense.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'tu-llave-secreta-wattsense-final'
db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")
os.makedirs('models', exist_ok=True)

# --- Modelos de Base de Datos ---
class Reading(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(50), nullable=False, index=True)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    voltage = db.Column(db.Float)
    current = db.Column(db.Float)
    power = db.Column(db.Float)
    def to_dict(self):
        data = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        if isinstance(data.get('timestamp'), datetime): data['timestamp'] = data['timestamp'].isoformat()
        return data

class Device(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(50), unique=True, nullable=False)
    device_name = db.Column(db.String(100))
    power_threshold = db.Column(db.Float)
    schedule_start = db.Column(db.Time, nullable=True)
    schedule_end = db.Column(db.Time, nullable=True)

class Alert(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(50), index=True)
    message = db.Column(db.String(500))
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    alert_type = db.Column(db.String(50))
    value = db.Column(db.Float)
    is_read = db.Column(db.Boolean, default=False)
    def to_dict(self):
        data = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        if isinstance(data.get('timestamp'), datetime): data['timestamp'] = data['timestamp'].isoformat()
        return data

# --- Estado del Sistema en Memoria ---
system_state = {
    "device_id": "licuadora_cocina",
    "relay_state": "OFF",
    "manual_override": True,
    "last_heartbeat": None
}

def generate_alerts(reading_data):
    with app.app_context():
        device = Device.query.filter_by(device_id=reading_data['device_id']).first()
        if not device: return

        power = reading_data['power']
        
        # --- Lógica de Alerta Única: Umbral de Potencia Excedido ---
        # Ahora, esta es la única condición que generará una alerta.
        # Se comprueba si se ha definido un umbral y si el consumo actual lo supera.
        if device.power_threshold and power > device.power_threshold:
            
            # Para no saturar con alertas, solo se crea una cada 10 minutos.
            ten_mins_ago = datetime.now(timezone.utc) - timedelta(minutes=10)
            existing = Alert.query.filter(
                Alert.device_id==device.device_id, 
                Alert.alert_type=='threshold', 
                Alert.timestamp > ten_mins_ago
            ).first()

            # Si no hay una alerta reciente, se crea una nueva.
            if not existing:
                alert_msg = f"Umbral de potencia excedido: {power:.2f}W > {device.power_threshold}W"
                new_alert = Alert(
                    device_id=device.device_id, 
                    message=alert_msg, 
                    alert_type='threshold', 
                    value=power
                )
                db.session.add(new_alert)
                db.session.commit()
                socketio.emit('new_alert', new_alert.to_dict())



def check_schedule():
    with app.app_context():
        if not system_state["manual_override"]:
            device = Device.query.filter_by(device_id=system_state["device_id"]).first()
            if device and device.schedule_start and device.schedule_end:
                now = datetime.now().time()
                current_relay_status = system_state["relay_state"]
                if device.schedule_start <= device.schedule_end:
                    should_be_on = device.schedule_start <= now < device.schedule_end
                else:
                    should_be_on = now >= device.schedule_start or now < device.schedule_end
                if should_be_on and current_relay_status == "OFF":
                    system_state["relay_state"] = "ON"
                    socketio.emit('state_update', get_full_system_state())
                elif not should_be_on and current_relay_status == "ON":
                    system_state["relay_state"] = "OFF"
                    socketio.emit('state_update', get_full_system_state())

def train_and_save_models():
    with app.app_context():
        logging.info("Iniciando entrenamiento de modelos de pronóstico...")
        try:
            # 1. ENFOQUE EN DATOS RECIENTES: Usamos solo las últimas 24 horas.
            # Esto ayuda al modelo a aprender de los patrones de uso más actuales.
            one_day_ago = datetime.now(timezone.utc) - timedelta(days=1)
            data = Reading.query.filter(Reading.timestamp >= one_day_ago).order_by(Reading.timestamp).all()
            
            # Mantenemos un mínimo de datos para asegurar que haya algo que entrenar.
            if len(data) < 100:
                logging.warning(f"No hay suficientes datos recientes para entrenar (se necesitan 100, hay {len(data)}).")
                return
            
            df = pd.DataFrame([d.to_dict() for d in data])
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df.set_index('timestamp', inplace=True)
            
            # 2. RE-MUESTREO MÁS PRECISO: Agrupamos en bloques de 1 minuto ('1T').
            # Esto preserva mucho mejor los picos de consumo cortos de la licuadora.
            power_series = df['power'].resample('1T').mean().fillna(method='ffill')

            if len(power_series) < 30:
                logging.warning(f"Datos insuficientes después de re-muestrear (se necesitan 30, hay {len(power_series)}).")
                return

            # El modelo de promedio móvil ahora será más reactivo a los cambios recientes.
            rolling_avg_model = power_series.rolling(window=12).mean().iloc[-1]
            if pd.isna(rolling_avg_model): rolling_avg_model = power_series.mean()
            joblib.dump(rolling_avg_model, os.path.join('models', 'rolling_avg_model.pkl'))
            logging.info("Modelo de Promedio Móvil guardado.")

            # El modelo ARIMA ahora tendrá datos más detallados para encontrar patrones.
            arima_model = ARIMA(power_series, order=(5,1,0), enforce_stationarity=False)
            arima_fit = arima_model.fit()
            joblib.dump(arima_fit, os.path.join('models', 'arima_model.pkl'))
            logging.info("Modelo ARIMA entrenado y guardado.")
        except Exception as e:
            logging.error(f"Error durante el entrenamiento de modelos: {e}", exc_info=True)




#fecha y hora 
# --- Rutas de la Aplicación (API y Frontend) ---
def get_full_system_state():
    last_reading_query = Reading.query.order_by(Reading.timestamp.desc()).first()
    is_connected = system_state["last_heartbeat"] and (datetime.now(timezone.utc) - system_state["last_heartbeat"]).total_seconds() < 15

    power = last_reading_query.power if last_reading_query and is_connected else 0
    voltage = last_reading_query.voltage if last_reading_query and is_connected else 0
    current = last_reading_query.current if last_reading_query and is_connected else 0
    
    today_start_utc = datetime.combine(datetime.now(timezone.utc).date(), time.min).replace(tzinfo=timezone.utc)
    readings_today = Reading.query.filter(Reading.timestamp >= today_start_utc).order_by(Reading.timestamp).all()
    kwh_today = 0
    if len(readings_today) > 1:
        power_watts = np.array([r.power for r in readings_today])
        timestamps_seconds = np.array([r.timestamp.timestamp() for r in readings_today])
        time_diffs_hours = np.diff(timestamps_seconds) / 3600.0
        avg_power_kw = (power_watts[:-1] + power_watts[1:]) / 2 / 1000.0
        kwh_today = np.sum(avg_power_kw * time_diffs_hours)

    device = Device.query.filter_by(device_id=system_state['device_id']).first()
    return {
        "power": power, "voltage": voltage, "current": current,
        "consumption_today_kwh": kwh_today,
        "device_name": device.device_name if device else "N/A",
        "power_threshold": device.power_threshold if device else 0.0,
        "relay_state": system_state['relay_state'],
        "manual_override": system_state['manual_override'],
        "schedule_on": device.schedule_start.strftime('%H:%M') if device and device.schedule_start else "",
        "schedule_off": device.schedule_end.strftime('%H:%M') if device and device.schedule_end else "",
    }

@app.route('/')
def dashboard(): return render_template('dashboard.html')

@app.route('/api/data')
def get_api_data(): return jsonify(get_full_system_state())

@app.route('/api/readings')
def get_readings():
    hours = request.args.get('hours', 24, type=int)
    time_ago = datetime.now(timezone.utc) - timedelta(hours=hours)
    readings = Reading.query.filter(Reading.timestamp >= time_ago).order_by(Reading.timestamp).all()
    return jsonify([r.to_dict() for r in readings])

@app.route('/api/forecast')
def get_forecast():
    model_type = request.args.get('model', 'rolling_avg')
    horizon_hours = request.args.get('horizon', 1, type=int)
    model_path = os.path.join('models', f'{model_type}_model.pkl')
    
    if not os.path.exists(model_path):
        return jsonify({"error": "El modelo seleccionado aún no está entrenado."}), 503

    steps_to_predict = horizon_hours * 12

    try:
        if model_type == 'arima':
            model_fit = joblib.load(model_path)
            forecast_values = model_fit.forecast(steps=steps_to_predict)
            forecast_values[forecast_values < 0] = 0
            forecast_data = forecast_values.tolist()
        else:
            last_avg = joblib.load(model_path)
            forecast_data = [last_avg] * steps_to_predict
        
        now = datetime.now(timezone.utc)
        time_labels = [(now + timedelta(minutes=i*5)).isoformat() for i in range(1, steps_to_predict + 1)]

        return jsonify({ "model": model_type, "labels": time_labels, "data": forecast_data })
    except Exception as e:
        logging.error(f"Error al generar pronóstico con {model_type}: {e}")
        return jsonify({"error": "No se pudo generar el pronóstico."}), 500

@app.route('/api/alerts', methods=['GET'])
def get_alerts():
    alerts = Alert.query.filter_by(is_read=False).order_by(Alert.timestamp.desc()).limit(20).all()
    device = Device.query.filter_by(device_id=system_state['device_id']).first()
    return jsonify({
        "alerts": [a.to_dict() for a in alerts],
        "device_name": device.device_name if device else "N/A"
    })

@app.route('/api/alerts/<int:alert_id>/read', methods=['POST'])
def mark_alert_as_read(alert_id):
    alert = Alert.query.get(alert_id)
    if alert:
        alert.is_read = True
        db.session.commit()
        return jsonify({"status": "success"})
    return jsonify({"error": "Alert not found"}), 404

@app.route('/api/alerts/read_all', methods=['POST'])
def mark_all_alerts_as_read():
    try:
        Alert.query.filter_by(is_read=False).update({"is_read": True})
        db.session.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/analysis')
def get_consumption_analysis():
    device = Device.query.filter_by(device_id=system_state['device_id']).first()
    if not device: return jsonify({"error": "Device not found"}), 404

    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
    readings = Reading.query.filter(Reading.device_id == device.device_id, Reading.timestamp >= seven_days_ago).all()

    if not readings:
        return jsonify({ "avg_power": 0, "recommendations": ["No hay suficientes datos para generar un análisis."] })

    df = pd.DataFrame([r.to_dict() for r in readings])
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    avg_power = df[df['power'] > 5.0]['power'].mean() if not df[df['power'] > 5.0].empty else 0
    
    recommendations = [
        "<b>Uso Eficiente:</b> Corta los alimentos sólidos en trozos pequeños antes de licuar para reducir el esfuerzo del motor.",
        "<b>Evita Sobrecalentamientos:</b> No uses la licuadora por más de 1-2 minutos seguidos. Dale pausas para evitar sobrecalentar el motor.",
        "<b>Líquido Adecuado:</b> Asegúrate de añadir suficiente líquido (agua, leche, jugo) para que los ingredientes se mezclen fácilmente y no fuercen las cuchillas."
    ]

    if avg_power > (device.power_threshold * 0.8):
        recommendations.insert(0, f"<b>Consumo Alto:</b> El consumo promedio ({avg_power:.2f}W) es cercano al máximo configurado. Considera licuar en porciones más pequeñas.")
    
    return jsonify({ "avg_power": avg_power, "recommendations": recommendations })

@app.route('/api/device/update', methods=['POST'])
def update_device():
    data = request.json
    device = Device.query.filter_by(device_id=system_state['device_id']).first()
    if device:
        try:
            device.device_name = data.get('device_name', device.device_name)
            device.power_threshold = float(data.get('power_threshold', device.power_threshold))
            db.session.commit()
            socketio.emit('state_update', get_full_system_state())
            return jsonify({"status": "success"})
        except (ValueError, TypeError) as e:
            db.session.rollback()
            return jsonify({"error": "Invalid data format"}), 400
    return jsonify({"error": "Device not found"}), 404

@app.route('/update', methods=['POST'])
def update_from_esp32():
    data = request.json
    now = datetime.now(timezone.utc)
    system_state["last_heartbeat"] = now
    
    power_factor = 0.9 
    power = data.get("voltage", 0.0) * data.get("current", 0.0) * power_factor
    
    try:
        new_reading = Reading(
            device_id=system_state["device_id"], timestamp=now, 
            voltage=data.get("voltage"), current=data.get("current"), power=power
        )
        db.session.add(new_reading)
        db.session.commit()
        socketio.emit('new_reading', new_reading.to_dict())
        generate_alerts(new_reading.to_dict())
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error al guardar lectura: {e}")
    
    check_schedule()
    return jsonify({"relay_state": system_state["relay_state"]})

@app.route('/control', methods=['POST'])
def control_from_web():
    data = request.json
    action = data.get("action")
    
    if action == "toggle_relay":
        system_state["relay_state"] = data.get("state", "OFF")
        system_state["manual_override"] = True
    elif action == "toggle_schedule":
        system_state["manual_override"] = not data.get("enabled", False)
        if not system_state["manual_override"]: check_schedule()
    elif action == "set_schedule":
        device = Device.query.filter_by(device_id=system_state["device_id"]).first()
        if device:
            try:
                device.schedule_start = datetime.strptime(data.get("on"), '%H:%M').time() if data.get("on") else None
                device.schedule_end = datetime.strptime(data.get("off"), '%H:%M').time() if data.get("off") else None
                db.session.commit()
                if not system_state["manual_override"]: check_schedule()
            except Exception as e:
                db.session.rollback(); logging.error(f"Error al guardar horario: {e}")
    
    socketio.emit('state_update', get_full_system_state())
    return jsonify({"status": "success"})

# --- Inicialización del Servidor ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if not Device.query.filter_by(device_id='licuadora_cocina').first():
            db.session.add(Device(
                device_id='licuadora_cocina', 
                device_name='Licuadora Cocina', 
                power_threshold=800.0, 
                schedule_start=time(8,0), 
                schedule_end=time(9,0)
            ))
            db.session.commit()
            
    scheduler = BackgroundScheduler(daemon=True, timezone='America/Mexico_City')
    scheduler.add_job(train_and_save_models, 'interval', hours=1, id='train_models_job')
    scheduler.add_job(check_schedule, 'interval', seconds=30, id='check_schedule_job')
    scheduler.start()
    
    logging.info("Servidor iniciado y planificador de tareas en ejecución.")
    with app.app_context():
        if Reading.query.count() > 200:
            train_and_save_models()
            
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)

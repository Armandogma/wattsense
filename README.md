# Wattsense - Monitoreo inteligente de energia

Wattsense es una aplicacion web para monitorear el consumo electrico de un dispositivo conectado, registrar lecturas en tiempo real y apoyar decisiones de ahorro de energia. El proyecto esta pensado para recibir mediciones desde un microcontrolador, por ejemplo un ESP32, y mostrarlas en un dashboard local construido con Flask.

## Para que sirve

- Visualizar consumo actual, voltaje, corriente y energia consumida durante el dia.
- Registrar lecturas electricas en una base de datos SQLite.
- Consultar historicos de consumo por 24 horas, 7 dias o 30 dias.
- Generar pronosticos de consumo con modelos de promedio movil y ARIMA.
- Configurar umbrales de potencia para detectar consumos altos.
- Emitir alertas cuando el consumo supera el limite configurado.
- Controlar el estado de un relevador de forma manual o por horario.
- Analizar el uso del dispositivo y mostrar recomendaciones de eficiencia.

## Tecnologias

- Python
- Flask
- Flask-SQLAlchemy
- Flask-SocketIO
- SQLite
- APScheduler
- Pandas, NumPy, Statsmodels y Joblib
- Bootstrap, Bootstrap Icons, Plotly y Socket.IO en el frontend

## Estructura principal

```text
wattsense_server.py      Servidor Flask, API, Socket.IO, base de datos y tareas programadas
templates/dashboard.html Dashboard web de monitoreo y control
rubeas.ipynb             Notebook de experimentacion y entrenamiento de modelos
models/                  Carpeta donde se generan los modelos entrenados localmente
firmware/esp32_wattsense Codigo del ESP32 para enviar lecturas y controlar el rele
```

## Instalacion

1. Crea y activa un entorno virtual:

```bash
python -m venv venv
venv\Scripts\activate
```

2. Instala las dependencias:

```bash
pip install -r requirements.txt
```

3. Ejecuta el servidor:

```bash
python wattsense_server.py
```

4. Abre el dashboard en:

```text
http://localhost:5000
```

## API principal

- `GET /` muestra el dashboard.
- `GET /api/data` devuelve el estado actual del sistema.
- `GET /api/readings?hours=24` devuelve lecturas historicas.
- `GET /api/forecast?model=rolling_avg&horizon=1` genera un pronostico.
- `GET /api/alerts` lista alertas pendientes.
- `POST /update` recibe lecturas del dispositivo.
- `POST /control` cambia el estado del relevador o la programacion.

Ejemplo de lectura enviada por un ESP32:

```json
{
  "voltage": 127.0,
  "current": 1.25
}
```

## Firmware ESP32

El codigo del ESP32 esta en `firmware/esp32_wattsense/esp32_wattsense.ino`.

Antes de compilarlo en Arduino IDE:

1. Copia `firmware/esp32_wattsense/secrets.example.h` como `firmware/esp32_wattsense/secrets.h`.
2. Edita `secrets.h` con tu red WiFi, contrasena y la IP del servidor Flask.
3. Instala la libreria `ArduinoJson` desde el Library Manager de Arduino IDE.
4. Carga el sketch en el ESP32.

`secrets.h` esta ignorado por Git para evitar subir credenciales al repositorio.

## Notas

La base de datos SQLite, el entorno virtual y los modelos entrenados se generan de forma local y no se suben al repositorio. Al iniciar la aplicacion, Flask crea las tablas necesarias y registra un dispositivo inicial llamado `Licuadora Cocina`.

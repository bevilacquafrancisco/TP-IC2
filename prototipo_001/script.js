// ===== CONFIGURACIÓN MQTT =====
const BROKER = "test.mosquitto.org";
const PORT = 8080;
const TOPIC_PUB = "brazo/comando";
const TOPIC_SUB = "brazo/estado";
const CLIENT_ID = "WebUser_" + Math.random().toString(16).substr(2, 8);

// ===== CONFIGURACIÓN DEL ROBOT =====
// ¡AJUSTA ESTOS VALORES SEGÚN TU MONTAJE FÍSICO!
const SERVO_PINZA_ID = 4;
const PINZA_ABIERTA = 180;  // Ángulo para abrir
const PINZA_CERRADA = 0;   // Ángulo para cerrar

// Cliente MQTT
const client = new Paho.MQTT.Client(BROKER, PORT, CLIENT_ID);

// ===== INICIALIZACIÓN =====
window.onload = function() {
    // Asignar eventos a los sliders
    setupSlider(1);
    setupSlider(2);
    setupSlider(3);
    setupSlider(4);

    // Asignar eventos a los botones
    document.getElementById("btn-abrir-pinza").addEventListener("click", () => sendCmd(SERVO_PINZA_ID, PINZA_ABIERTA));
    document.getElementById("btn-cerrar-pinza").addEventListener("click", () => sendCmd(SERVO_PINZA_ID, PINZA_CERRADA));
    document.getElementById("btn-home").addEventListener("click", resetHome);

    // Conectar MQTT
    conectarMQTT();
};

// ===== FUNCIONES MQTT =====
function conectarMQTT() {
    log("Conectando a MQTT...");
    client.connect({
        onSuccess: function() {
            document.getElementById("connection-status").innerText = "Conectado a Broker Público 🟢";
            document.getElementById("connection-status").classList.add("online");
            log("✅ Conectado exitosamente.");
            client.subscribe(TOPIC_SUB);
        },
        onFailure: function(err) {
            log("❌ Error de conexión: " + err.errorMessage);
        },
        useSSL: false
    });

    client.onConnectionLost = function(resp) {
        document.getElementById("connection-status").innerText = "Desconectado 🔴";
        document.getElementById("connection-status").classList.remove("online");
        log("Conexión perdida: " + resp.errorMessage);
    };

    client.onMessageArrived = function(message) {
        try {
            // 1. Intentamos convertir el mensaje de texto (JSON) a un objeto útil
            const datos = JSON.parse(message.payloadString);

            // 2. Definimos los nombres para que sea más amigable
            // El índice 0 está vacío porque los servos van del 1 al 4
            const nombresServos = ["", "Base", "Hombro", "Codo", "Pinza"];

            // 3. Verificamos si es el mensaje de confirmación de movimiento
            if (datos.msg === "movimiento_ok") {
                // Obtenemos el nombre. Si el ID es raro, usamos "Servo X" por defecto
                const nombreServo = nombresServos[datos.servo] || ("Servo " + datos.servo);
                
                // 4. Formateamos el mensaje bonito
                const mensajeBonito = `[ESP32 dice] Comando recibido. ${nombreServo} → Moviendo a ${datos.angulo_actual}°`;
                
                // Lo mostramos con un ícono diferente para confirmar éxito
                log("✅ " + mensajeBonito);
            } 
            else if (datos.msg === "ESP32 Online") {
                log("🚀 [ESP32]: Sistema iniciado y listo.");
            }
            else {
                // Si es otro JSON desconocido, lo mostramos normal
                log("📥 [ESP32 Datos]: " + message.payloadString);
            }

        } catch (e) {
            // Si el mensaje NO es JSON (es texto plano o hubo error), lo mostramos tal cual
            log("📥 [ESP32]: " + message.payloadString);
        }
    };
}

function sendCmd(servoId, angle) {
    if (!client.isConnected()) {
        log("⚠️ No conectado a MQTT");
        return;
    }

    // Actualizar también el slider visualmente si el comando vino de un botón
    const slider = document.getElementById("slider" + servoId);
    const label = document.getElementById("val" + servoId);
    if(slider) slider.value = angle;
    if(label) label.innerText = angle;

    let data = JSON.stringify({
        "servo": parseInt(servoId),
        "angulo": parseInt(angle)
    });
    
    let message = new Paho.MQTT.Message(data);
    message.destinationName = TOPIC_PUB;
    client.send(message);
    log("📤 Enviado: " + data);
}

// ===== UTILIDADES DE INTERFAZ =====
function setupSlider(id) {
    const slider = document.getElementById("slider" + id);
    const label = document.getElementById("val" + id);

    // "input": Actualiza el número mientras arrastras
    slider.addEventListener("input", function() {
        label.innerText = this.value;
    });

    // "change": Envía el comando solo cuando sueltas el slider (para no saturar MQTT)
    slider.addEventListener("change", function() {
        sendCmd(id, this.value);
    });
}

function resetHome() {
    sendCmd(1, 90); 
    setTimeout(()=>sendCmd(2,90), 200);
    setTimeout(()=>sendCmd(3,90), 400); 
    setTimeout(()=>sendCmd(4,90), 600);
}

function log(msg) {
    let logDiv = document.getElementById("log");
    let time = new Date().toLocaleTimeString();
    logDiv.innerHTML += `<div><span style="color:#888">[${time}]</span> ${msg}</div>`;
    logDiv.scrollTop = logDiv.scrollHeight;
}
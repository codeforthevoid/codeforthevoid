class Terminal {
    static CONNECTION_TIMEOUT = 5000;
    static RECONNECT_ATTEMPTS = 3;
    static MESSAGE_TYPES = {
        SYSTEM: 'system',
        ERROR: 'error',
        WARNING: 'warning',
        SUCCESS: 'success',
        MESSAGE: 'message'
    };

    constructor(terminalId, websocketUrl, options = {}) {
        this.terminalId = terminalId;
        this.websocketUrl = websocketUrl;
        this.options = {
            autoReconnect: true,
            maxRetries: Terminal.RECONNECT_ATTEMPTS,
            reconnectInterval: Terminal.CONNECTION_TIMEOUT,
            maxHistory: 1000,
            ...options
        };

        this.websocket = null;
        this.messageHistory = [];
        this.isConnected = false;
        this.connectAttempts = 0;
        this.messageQueue = [];

        this.terminalElement = document.getElementById(terminalId);
        this.outputElement = this.terminalElement.querySelector('.terminal-output');

        this.boundHandleVisibilityChange = this.handleVisibilityChange.bind(this);
        this.boundHandleOnline = this.handleOnline.bind(this);
        this.boundHandleOffline = this.handleOffline.bind(this);

        this.initializeEventListeners();
    }

    initializeEventListeners() {
        document.addEventListener('visibilitychange', this.boundHandleVisibilityChange);
        window.addEventListener('online', this.boundHandleOnline);
        window.addEventListener('offline', this.boundHandleOffline);
    }

    async connect() {
        if (this.websocket?.readyState === WebSocket.CONNECTING) {
            return;
        }

        try {
            this.websocket = new WebSocket(`${this.websocketUrl}/ws/${this.terminalId}`);
            this.setupWebSocketHandlers();

            await this.waitForConnection();
            this.connectAttempts = 0;
            this.processMessageQueue();

        } catch (error) {
            this.handleConnectionError(error);
        }
    }

    setupWebSocketHandlers() {
        this.websocket.onopen = () => {
            this.isConnected = true;
            this.appendMessage('System', 'Connected to the void...', Terminal.MESSAGE_TYPES.SUCCESS);
            this.emit('connected');
        };

        this.websocket.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
                this.handleIncomingMessage(message);
            } catch (error) {
                console.error('Error parsing message:', error);
            }
        };

        this.websocket.onclose = (event) => {
            this.handleDisconnection(event);
        };

        this.websocket.onerror = (error) => {
            this.handleWebSocketError(error);
        };
    }

    async waitForConnection() {
        return new Promise((resolve, reject) => {
            const timeout = setTimeout(() => {
                reject(new Error('Connection timeout'));
            }, Terminal.CONNECTION_TIMEOUT);

            this.websocket.addEventListener('open', () => {
                clearTimeout(timeout);
                resolve();
            }, { once: true });

            this.websocket.addEventListener('error', () => {
                clearTimeout(timeout);
                reject(new Error('Connection failed'));
            }, { once: true });
        });
    }

    handleIncomingMessage(message) {
        const { sender, content, type = Terminal.MESSAGE_TYPES.MESSAGE, timestamp } = message;
        this.appendMessage(sender, content, type, new Date(timestamp));
        this.emit('message', message);
    }

    appendMessage(sender, content, type = Terminal.MESSAGE_TYPES.MESSAGE, timestamp = new Date()) {
        const messageElement = document.createElement('div');
        messageElement.className = `terminal-message ${type}`;
        messageElement.innerHTML = this.sanitizeHTML`
            <span class="timestamp">[${timestamp.toISOString()}]</span>
            <span class="sender">${sender}:</span>
            <span class="content">${content}</span>
        `;

        this.outputElement.appendChild(messageElement);
        this.scrollToBottom();

        this.addToHistory({
            sender,
            content,
            type,
            timestamp
        });
    }

    sanitizeHTML(strings, ...values) {
        const clean = values.map(value =>
            String(value)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#039;')
        );

        return strings.reduce((result, string, i) =>
            result + string + (clean[i] || ''), '');
    }

    async sendMessage(content) {
        const message = {
            sender: this.terminalId,
            content,
            timestamp: new Date().toISOString(),
            type: Terminal.MESSAGE_TYPES.MESSAGE
        };

        if (!this.isConnected) {
            this.messageQueue.push(message);
            if (this.options.autoReconnect) {
                await this.connect();
            }
            return;
        }

        try {
            await this.sendToWebSocket(message);
            this.emit('messageSent', message);
        } catch (error) {
            this.handleSendError(error, message);
        }
    }

    async sendToWebSocket(message) {
        return new Promise((resolve, reject) => {
            try {
                this.websocket.send(JSON.stringify(message));
                resolve();
            } catch (error) {
                reject(error);
            }
        });
    }

    addToHistory(message) {
        this.messageHistory.push(message);
        if (this.messageHistory.length > this.options.maxHistory) {
            this.messageHistory.shift();
        }
    }

    scrollToBottom() {
        requestAnimationFrame(() => {
            this.outputElement.scrollTop = this.outputElement.scrollHeight;
        });
    }

    handleConnectionError(error) {
        console.error('WebSocket connection error:', error);
        this.appendMessage('System', `Connection error: ${error.message}`, Terminal.MESSAGE_TYPES.ERROR);

        if (this.options.autoReconnect && this.connectAttempts < this.options.maxRetries) {
            this.connectAttempts++;
            setTimeout(() => this.connect(), this.options.reconnectInterval);
        }
    }

    handleDisconnection(event) {
        this.isConnected = false;
        this.appendMessage('System', 'Disconnected from the void...', Terminal.MESSAGE_TYPES.WARNING);
        this.emit('disconnected', event);

        if (this.options.autoReconnect && this.connectAttempts < this.options.maxRetries) {
            this.connectAttempts++;
            setTimeout(() => this.connect(), this.options.reconnectInterval);
        }
    }

    handleWebSocketError(error) {
        console.error('WebSocket error:', error);
        this.appendMessage('System', 'Connection error occurred', Terminal.MESSAGE_TYPES.ERROR);
        this.emit('error', error);
    }

    handleSendError(error, message) {
        console.error('Message send error:', error);
        this.appendMessage('System', 'Failed to send message', Terminal.MESSAGE_TYPES.ERROR);
        this.messageQueue.push(message);
    }

    handleVisibilityChange() {
        if (document.visibilityState === 'visible' && !this.isConnected) {
            this.connect();
        }
    }

    handleOnline() {
        if (!this.isConnected) {
            this.connect();
        }
    }

    handleOffline() {
        this.appendMessage('System', 'Network connection lost', Terminal.MESSAGE_TYPES.WARNING);
    }

    async processMessageQueue() {
        while (this.messageQueue.length > 0 && this.isConnected) {
            const message = this.messageQueue.shift();
            try {
                await this.sendToWebSocket(message);
            } catch (error) {
                this.messageQueue.unshift(message);
                break;
            }
        }
    }

    emit(eventName, data) {
        const event = new CustomEvent(`terminal:${eventName}`, { detail: data });
        this.terminalElement.dispatchEvent(event);
    }

    destroy() {
        document.removeEventListener('visibilitychange', this.boundHandleVisibilityChange);
        window.removeEventListener('online', this.boundHandleOnline);
        window.removeEventListener('offline', this.boundHandleOffline);

        if (this.websocket) {
            this.websocket.close();
        }
    }
}

class VoidEffect {
    static DEFAULT_PARTICLES = 100;
    static FADE_SPEED = 0.1;
    static MIN_SPEED = 0.5;
    static MAX_SPEED = 1.5;
    static MIN_SIZE = 1;
    static MAX_SIZE = 3;

    constructor(canvasId, options = {}) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext('2d');
        this.particles = [];
        this.isRunning = false;
        this.options = {
            particleCount: VoidEffect.DEFAULT_PARTICLES,
            fadeSpeed: VoidEffect.FADE_SPEED,
            minSpeed: VoidEffect.MIN_SPEED,
            maxSpeed: VoidEffect.MAX_SPEED,
            minSize: VoidEffect.MIN_SIZE,
            maxSize: VoidEffect.MAX_SIZE,
            color: 'rgba(255, 255, 255, 0.5)',
            ...options
        };

        this.boundAnimate = this.animate.bind(this);
        this.boundHandleResize = this.handleResize.bind(this);

        this.initialize();
    }

    initialize() {
        this.handleResize();
        this.createParticles();
        this.setupEventListeners();
        this.start();
    }

    setupEventListeners() {
        window.addEventListener('resize', this.boundHandleResize);
    }

    handleResize() {
        this.canvas.width = window.innerWidth;
        this.canvas.height = window.innerHeight;

        // Re-create particles on resize to maintain density
        this.createParticles();
    }

    createParticles() {
        this.particles = Array.from({ length: this.options.particleCount }, () => this.createParticle());
    }

    createParticle(y = undefined) {
        return {
            x: Math.random() * this.canvas.width,
            y: y ?? (Math.random() * this.canvas.height),
            speed: this.options.minSpeed + Math.random() * (this.options.maxSpeed - this.options.minSpeed),
            size: this.options.minSize + Math.random() * (this.options.maxSize - this.options.minSize)
        };
    }

    start() {
        if (!this.isRunning) {
            this.isRunning = true;
            requestAnimationFrame(this.boundAnimate);
        }
    }

    stop() {
        this.isRunning = false;
    }

    animate() {
        if (!this.isRunning) return;

        // Clear with fade effect
        this.ctx.fillStyle = `rgba(0, 0, 0, ${this.options.fadeSpeed})`;
        this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);

        // Update and draw particles
        this.particles.forEach(particle => {
            // Update position
            particle.y += particle.speed;

            // Reset particle if it goes off screen
            if (particle.y > this.canvas.height) {
                Object.assign(particle, this.createParticle(0));
            }

            // Draw particle
            this.ctx.fillStyle = this.options.color;
            this.ctx.fillRect(
                Math.round(particle.x),
                Math.round(particle.y),
                particle.size,
                particle.size
            );
        });

        // Request next frame
        requestAnimationFrame(this.boundAnimate);
    }

    destroy() {
        this.stop();
        window.removeEventListener('resize', this.boundHandleResize);
        this.particles = [];
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    }

    // Additional effects
    addBurst(x, y, count = 10) {
        const burstParticles = Array.from({ length: count }, () => ({
            x,
            y,
            speed: this.options.maxSpeed * 2,
            size: this.options.maxSize,
            angle: Math.random() * Math.PI * 2
        }));

        const animateBurst = () => {
            burstParticles.forEach(particle => {
                particle.x += Math.cos(particle.angle) * particle.speed;
                particle.y += Math.sin(particle.angle) * particle.speed;
                particle.speed *= 0.95;

                this.ctx.fillStyle = this.options.color;
                this.ctx.fillRect(
                    Math.round(particle.x),
                    Math.round(particle.y),
                    particle.size,
                    particle.size
                );
            });

            if (burstParticles[0].speed > 0.1) {
                requestAnimationFrame(animateBurst);
            }
        };

        requestAnimationFrame(animateBurst);
    }
}
let pollingInterval = null;
let sessionId = null;

// Инициализация
document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('candidateForm');
    const sendBtn = document.getElementById('sendBtn');
    const stopBtn = document.getElementById('stopBtn');
    const userInput = document.getElementById('userInput');

    form.addEventListener('submit', startInterview);
    sendBtn.addEventListener('click', sendMessage);
    stopBtn.addEventListener('click', stopInterview);
    userInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            sendMessage();
        }
    });
});

async function startInterview(e) {
    e.preventDefault();
    
    const formData = {
        team_name: "Team Alpha", // Значение по умолчанию
        candidate: {
            name: document.getElementById('candidateName').value,
            position: document.getElementById('position').value,
            grade: document.getElementById('grade').value,
            experience: document.getElementById('experience').value
        },
        config: 'config/runtime.json'
    };

    try {
        const response = await fetch('/api/start', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(formData)
        });

        const data = await response.json();
        if (data.session_id) {
            sessionId = data.session_id;
            document.getElementById('startForm').classList.add('hidden');
            document.getElementById('interviewPanel').classList.remove('hidden');
            showThinkingIndicator('Подготовка первого вопроса...');
            startPolling();
        }
    } catch (error) {
        console.error('Error starting interview:', error);
        alert('Ошибка при запуске интервью: ' + error.message);
    }
}

function startPolling() {
    if (pollingInterval) {
        clearInterval(pollingInterval);
    }
    
    pollingInterval = setInterval(async () => {
        try {
            const response = await fetch('/api/poll');
            const data = await response.json();
            
            if (data.messages && data.messages.length > 0) {
                data.messages.forEach(msg => {
                    console.log('Received message:', msg.type, msg); // Отладка
                    handleMessage(msg);
                });
            }
        } catch (error) {
            console.error('Error polling:', error);
        }
    }, 500); // Опрашиваем каждые 500мс
}

function handleMessage(msg) {
    const container = document.getElementById('messagesContainer');
    
    // Обрабатываем финальный отчёт ПЕРЕД обработкой stop/completed
    // чтобы убедиться, что отчёт отображается даже если пришло сообщение stop
    if (msg.type === 'final_report') {
        console.log('Processing final_report:', msg); // Отладка
        hideThinkingIndicator();
        // Убираем сообщение о статусе перед показом отчета
        const statusMessages = container.querySelectorAll('.message.status');
        statusMessages.forEach(msg => msg.remove());
        
        if (msg.data) {
            try {
                showFinalReport(msg.data);
                console.log('Final report displayed successfully');
            } catch (error) {
                console.error('Error displaying final report:', error);
                addMessage('error', 'Ошибка при отображении финального отчёта: ' + error.message);
            }
        } else {
            console.error('final_report received but data is missing!', msg);
            addMessage('error', 'Финальный отчёт получен, но данные отсутствуют');
        }
        // Останавливаем polling после получения финального отчёта
        if (pollingInterval) {
            clearInterval(pollingInterval);
            pollingInterval = null;
            console.log('Polling stopped after receiving final_report');
        }
        // Скрываем поле ввода после получения отчёта
        const inputArea = document.querySelector('.input-area');
        if (inputArea) {
            inputArea.style.display = 'none';
        }
        return;
    }
    
    if (msg.type === 'stop' || msg.type === 'completed') {
        // При получении stop/completed НЕ останавливаем polling сразу,
        // так как финальный отчёт может прийти позже
        // Просто скрываем поле ввода и показываем индикатор ожидания
        const inputArea = document.querySelector('.input-area');
        if (inputArea) {
            inputArea.style.display = 'none';
        }
        // Показываем индикатор ожидания финального отчёта
        if (msg.type === 'stop') {
            showThinkingIndicator('Генерация финального отчёта...');
        }
        // Polling продолжит работать до получения final_report
        return;
    }
    
    if (msg.type === 'error') {
        addMessage('error', 'Ошибка: ' + msg.text);
        return;
    }
    
    if (msg.type === 'status') {
        showThinkingIndicator(msg.text);
        // Обновляем последнее сообщение статуса, если оно есть, иначе добавляем новое
        const lastMessage = container.lastElementChild;
        if (lastMessage && lastMessage.classList.contains('status')) {
            lastMessage.querySelector('div:last-child').textContent = msg.text;
        } else {
            addMessage('status', msg.text);
        }
        return;
    }
    
    if (msg.type === 'internal') {
        hideThinkingIndicator();
        // Определяем агента по тексту сообщения и убираем префикс
        let agentType = 'internal';
        let messageText = msg.text;
        
        if (msg.text.startsWith('Observer:')) {
            agentType = 'observer';
            messageText = msg.text.replace(/^Observer:\s*/, ''); // Убираем префикс
        } else if (msg.text.startsWith('Interviewer:')) {
            agentType = 'interviewer-internal';
            messageText = msg.text.replace(/^Interviewer:\s*/, ''); // Убираем префикс
        }
        addMessage(agentType, messageText);
    } else {
        hideThinkingIndicator();
        addMessage(msg.type === 'user' ? 'user' : 'interviewer', msg.text);
    }
}

function addMessage(type, text) {
    const container = document.getElementById('messagesContainer');
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${type}`;
    
    const label = document.createElement('div');
    label.className = 'message-label';
    
    switch(type) {
        case 'interviewer':
            label.textContent = 'Interviewer';
            break;
        case 'user':
            label.textContent = 'Вы';
            break;
        case 'observer':
            label.textContent = '[Observer]';
            break;
        case 'interviewer-internal':
            label.textContent = '[Interviewer]';
            break;
        case 'internal':
            label.textContent = '[Internal]';
            break;
        case 'status':
            label.textContent = 'Статус';
            break;
        case 'error':
            label.textContent = 'Ошибка';
            break;
    }
    
    const content = document.createElement('div');
    content.textContent = text;
    
    messageDiv.appendChild(label);
    messageDiv.appendChild(content);
    container.appendChild(messageDiv);
    
    // Прокрутка вниз
    container.scrollTop = container.scrollHeight;
}

async function sendMessage() {
    const input = document.getElementById('userInput');
    const message = input.value.trim();
    
    if (!message) return;
    
    // Показываем сообщение пользователя
    addMessage('user', message);
    input.value = '';
    
    // Показываем индикатор "думает" после отправки сообщения
    showThinkingIndicator('Анализирую ответ...');
    
    try {
        const response = await fetch('/api/message', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ message: message })
        });
        
        if (!response.ok) {
            throw new Error('Failed to send message');
        }
    } catch (error) {
        console.error('Error sending message:', error);
        alert('Ошибка при отправке сообщения: ' + error.message);
    }
}

async function stopInterview() {
    try {
        await fetch('/api/stop', { method: 'POST' });
        // НЕ останавливаем polling здесь - он должен продолжаться до получения финального отчёта
        // Polling остановится автоматически при получении final_report или completed
    } catch (error) {
        console.error('Error stopping interview:', error);
    }
}

function showThinkingIndicator(text = 'Агент думает...') {
    const indicator = document.getElementById('thinkingIndicator');
    if (indicator) {
        const textElement = indicator.querySelector('.thinking-text');
        if (textElement) {
            textElement.textContent = text;
        }
        indicator.classList.remove('hidden');
    }
}

function hideThinkingIndicator() {
    const indicator = document.getElementById('thinkingIndicator');
    if (indicator) {
        indicator.classList.add('hidden');
    }
}

function resetInterview() {
    // Останавливаем polling
    if (pollingInterval) {
        clearInterval(pollingInterval);
        pollingInterval = null;
    }
    
    // Очищаем контейнер сообщений
    const messagesContainer = document.getElementById('messagesContainer');
    if (messagesContainer) {
        messagesContainer.innerHTML = '';
    }
    
    // Скрываем панели интервью и отчёта
    const interviewPanel = document.getElementById('interviewPanel');
    const reportPanel = document.getElementById('reportPanel');
    if (interviewPanel) {
        interviewPanel.classList.add('hidden');
    }
    if (reportPanel) {
        reportPanel.classList.add('hidden');
    }
    
    // Показываем форму начала интервью
    const startForm = document.getElementById('startForm');
    if (startForm) {
        startForm.classList.remove('hidden');
    }
    
    // Сбрасываем sessionId
    sessionId = null;
    
    // Очищаем форму (опционально - можно оставить заполненные значения)
    const form = document.getElementById('candidateForm');
    if (form) {
        form.reset();
        // Восстанавливаем значения по умолчанию
        document.getElementById('position').value = 'Backend Developer';
        document.getElementById('grade').value = 'Junior';
    }
    
    // Скрываем индикатор мышления
    hideThinkingIndicator();
    
    // Показываем поле ввода (на случай, если оно было скрыто)
    const inputArea = document.querySelector('.input-area');
    if (inputArea) {
        inputArea.style.display = '';
    }
}

function showFinalReport(data) {
    const reportPanel = document.getElementById('reportPanel');
    const reportContent = document.getElementById('reportContent');
    
    document.getElementById('interviewPanel').classList.add('hidden');
    reportPanel.classList.remove('hidden');
    
    let html = '';
    
    // Вердикт
    const verdict = data.verdict || {};
    const recommendation = verdict.recommendation || 'N/A';
    const verdictClass = recommendation.toLowerCase().replace(' ', '-');
    
    html += `<div class="verdict ${verdictClass}">`;
    html += `<h3>${recommendation}</h3>`;
    html += `<p>Грейд: ${verdict.grade || 'N/A'}</p>`;
    html += `<p>Уверенность: ${verdict.confidence_score || 0}%</p>`;
    html += `</div>`;
    
    // Технический обзор
    const technical = data.technical_review || {};
    html += `<div class="report-section">`;
    html += `<h3>Технический обзор</h3>`;
    
    if (technical.confirmed_skills && technical.confirmed_skills.length > 0) {
        html += `<h4>Подтверждённые навыки:</h4>`;
        html += `<ul class="skill-list">`;
        technical.confirmed_skills.forEach(skill => {
            html += `<li class="confirmed">${skill}</li>`;
        });
        html += `</ul>`;
    }
    
    if (technical.knowledge_gaps && technical.knowledge_gaps.length > 0) {
        html += `<h4>Пробелы в знаниях:</h4>`;
        html += `<ul class="skill-list">`;
        technical.knowledge_gaps.forEach(gap => {
            html += `<li class="gap">${gap}</li>`;
        });
        html += `</ul>`;
    }
    
    if (technical.topics && technical.topics.length > 0) {
        html += `<h4>Детали по темам:</h4>`;
        technical.topics.forEach(topic => {
            const status = topic.status || 'unknown';
            html += `<div class="topic-item">`;
            html += `<strong>${topic.topic || 'N/A'}</strong>`;
            html += `<span class="status-badge ${status}">${status}</span>`;
            if (topic.notes) {
                html += `<p>${topic.notes}</p>`;
            }
            if (topic.correct_answer) {
                html += `<p><em>Правильный ответ: ${topic.correct_answer}</em></p>`;
            }
            html += `</div>`;
        });
    }
    
    html += `</div>`;
    
    // Soft skills
    const softSkills = data.soft_skills || {};
    html += `<div class="report-section">`;
    html += `<h3>Soft Skills</h3>`;
    html += `<p><strong>Ясность:</strong> ${softSkills.clarity || 'N/A'}</p>`;
    html += `<p><strong>Честность:</strong> ${softSkills.honesty || 'N/A'}</p>`;
    html += `<p><strong>Вовлечённость:</strong> ${softSkills.engagement || 'N/A'}</p>`;
    html += `</div>`;
    
    // Roadmap
    if (data.personal_roadmap && data.personal_roadmap.length > 0) {
        html += `<div class="report-section">`;
        html += `<h3>Персональный Roadmap</h3>`;
        data.personal_roadmap.forEach(item => {
            html += `<div class="roadmap-item">`;
            html += `<h4>${item.topic || 'N/A'}</h4>`;
            if (item.resources && item.resources.length > 0) {
                html += `<ul>`;
                item.resources.forEach(resource => {
                    html += `<li>${resource}</li>`;
                });
                html += `</ul>`;
            }
            html += `</div>`;
        });
        html += `</div>`;
    }
    
    // Кнопка "Начать заново"
    html += `<div class="report-actions">`;
    html += `<button id="restartBtn" class="btn btn-primary">Начать заново</button>`;
    html += `</div>`;
    
    reportContent.innerHTML = html;
    
    // Добавляем обработчик для кнопки "Начать заново"
    const restartBtn = document.getElementById('restartBtn');
    if (restartBtn) {
        restartBtn.addEventListener('click', resetInterview);
    }
}

import logging
import os

from aiogram import Bot, Dispatcher, types, F
from aiogram.dispatcher.router import Router
from aiogram.filters import Command
import paramiko
import asyncio

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, InputFile, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

import kbrds
from functions.monitor import monitor_file

router = Router()

from functions.scheduler_interface import submit_job, show_queue, cancel_job
from config import user_ssh_clients
from config import monitoring_tasks
from functions.file_handling import upload_file, download_file
from functions.metrics import get_metrics

class CommandState(StatesGroup):
    awaiting_credentials = State()
    awaiting_ssh_details = State()
    awaiting_pem_file = State()
    waiting_for_command = State()
    awaiting_password = State()
    setting_monitoring_path = State()
    monitoring = State()
    waiting_for_job = State()
    waiting_for_download_filename = State()
    waiting_for_upload_file = State()
    awaiting_metric_selection = State()
    confirming = State()

def setup_handlers(router: Router):
    from main import bot, saved_connection_details

    @router.message(Command(commands=['start']))
    async def send_welcome(message: types.Message):
        await message.answer(
            "Привет! Я бот для SSH подключения.\n\n"
            "Для начала работы подключитесь к серверу используя  команду /connect"
        )

    @router.message(Command(commands=['help']))
    async def send_info(message: types.Message):
        await message.answer(
            "Доступные команды:\n"
            "/connect - Подключиться к серверу.\n"
            "/disconnect - Отключиться от сервера.\n"
            "/execute - Выполнить команду на сервере.\n"
            "/upload - Загрузить файл на сервер.\n"
            "/download - Скачать файл с сервера.\n"
            "/submit_job - Отправить задачу на выполнение.\n"
            "/show_queue - Просмотр очереди задач.\n"
            "/cancel_job - Отменить задачу.\n"
            "/add_monitoring - Начать мониторинг файла по установленному пути.\n"
            "/stop_monitoring - Остановить мониторинг файла.\n"
            "\nИспользуйте эти команды для управления вашими SSH подключениями и задачами."
        )

    @router.message(Command(commands=['connect']))
    async def connect_handler(message: types.Message, state: FSMContext):
        user_id = message.from_user.id
        await message.answer("Введите данные для подключения в формате: username host [port]")
        await state.set_state(CommandState.awaiting_credentials)

    @router.message(CommandState.awaiting_credentials)
    async def connect_ssh(message: types.Message, state: FSMContext):
        user_id = message.from_user.id
        parts = message.text.split()
        if len(parts) == 2 or len(parts) == 3:
            if user_id not in saved_connection_details:
                saved_connection_details[user_id] = {}
            saved_connection_details[user_id]["login"]=parts[0]
            saved_connection_details[user_id]["host"]=parts[1]
            saved_connection_details[user_id]["port"]=int(parts[2]) if len(parts) > 2 else 2222
            markup = InlineKeyboardMarkup(inline_keyboard=kbrds.keyboard_connecting)
            await message.answer("Выберите метод аутентификации:", reply_markup=markup)
            await state.clear()
        else:
            await message.answer("Введите данные для подключения в формате: username host ")

    @router.callback_query(F.data == 'auth_pem')
    async def process_auth_method(callback: CallbackQuery, state: FSMContext):
        await callback.message.edit_reply_markup()
        await callback.message.answer("Отправьте ваш .pem файл")
        await state.set_state(CommandState.awaiting_pem_file)

    @router.callback_query(F.data == 'auth_password')
    async def process_auth_method(callback: CallbackQuery, state: FSMContext):
        await callback.message.edit_reply_markup()
        await state.set_state(CommandState.awaiting_password)
        await callback.message.answer(
            "Введите данные для подключения в формате: password")

    @router.message(CommandState.awaiting_pem_file)
    async def receive_pem_file(message: types.Message, state: FSMContext):
        try:
            document = message.document
            file_id = document.file_id
            file_name = document.file_name
            if not document.file_name.endswith('.pem'):
                await message.answer("Пожалуйста, отправьте файл с расширением .pem")
                return
            file_path = f'./downloads/{document.file_name}'
            os.makedirs('./downloads', exist_ok=True)

            file = await bot.get_file(file_id)
            file_path_telegram = file.file_path

            await bot.download_file(file_path_telegram, destination=file_path)
            await state.update_data(pem_file=file_path)
            await state.set_state(CommandState.awaiting_ssh_details)
            await message.answer("Введите данные для подключения в формате: password")
        except Exception as e:
            pass

    @router.message(CommandState.awaiting_ssh_details)
    async def connect_with_pem(message: types.Message, state: FSMContext):
        user_id = message.from_user.id
        data = await state.get_data()
        pem_file = data['pem_file']
        username = saved_connection_details[user_id]["login"]
        host = saved_connection_details[user_id]["host"]
        port = saved_connection_details[user_id]["port"]
        parts = message.text.split()
        if len(parts) == 1:
            password = parts[0]

            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                key = paramiko.RSAKey.from_private_key_file(pem_file, password)
                ssh_client.connect(hostname=host, username=username, pkey=key, port=port)
                user_id = message.from_user.id
                user_ssh_clients[user_id] = ssh_client
                await message.answer("Успешное подключение к серверу.")
                await message.answer(
                    "Доступные команды:\n"
                    "/disconnect - Отключиться от сервера.\n"
                    "/execute - Выполнить команду на сервере.\n"
                    "/upload - Загрузить файл на сервер.\n"
                    "/download - Скачать файл с сервера.\n"
                    "/submit_job - Отправить задачу на выполнение.\n"
                    "/show_queue - Просмотр очереди задач.\n"
                    "/cancel_job - Отменить задачу.\n"
                    "/add_monitoring - Начать мониторинг файла по установленному пути.\n"
                    "/stop_monitoring - Остановить мониторинг файла.\n"
                    "\nИспользуйте эти команды для управления вашими SSH подключениями и задачами."
                )
                await state.clear()
            except Exception as e:
                await message.answer(f"Ошибка подключения: {e}")
                await state.clear()
        else:
            await message.answer("Введите данные для подключения в формате: password")

    @router.message(CommandState.awaiting_password)
    async def process_password(message: types.Message, state: FSMContext):
        user_id = message.from_user.id
        # password = message.text
        username = saved_connection_details[user_id]["login"]
        host = saved_connection_details[user_id]["host"]
        port = saved_connection_details[user_id]["port"]
        parts = message.text.split()
        if len(parts) == 1:
            await state.clear()
            password = parts[0]
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                ssh_client.connect(hostname=host, username=username, password=password, port=port)
                user_ssh_clients[user_id] = ssh_client
                await message.answer("Успешное подключение к серверу.")
                await message.answer(
                    "Доступные команды:\n"
                    "/connect - Подключиться к серверу.\n"
                    "/disconnect - Отключиться от сервера.\n"
                    "/execute - Выполнить команду на сервере.\n"
                    "/upload - Загрузить файл на сервер.\n"
                    "/download - Скачать файл с сервера.\n"
                    "/submit_job - Отправить задачу на выполнение.\n"
                    "/show_queue - Просмотр очереди задач.\n"
                    "/cancel_job - Отменить задачу.\n"
                    "/add_monitoring - Начать мониторинг файла по установленному пути.\n"
                    "/stop_monitoring - Остановить мониторинг файла.\n"
                    "\nИспользуйте эти команды для управления вашими SSH подключениями и задачами."
                )
            except Exception as e:
                await message.answer(f"Ошибка подключения: {e}")
        else:
            await message.answer("Введите данные для подключения в формате: password")

    @router.message(Command(commands=['disconnect']))
    async def disconnect_ssh(message: types.Message):
        user_id = message.from_user.id
        if user_id not in user_ssh_clients:
            await message.answer("Сначала подключитесь к серверу.")
            return
        if user_id in monitoring_tasks:
            task = monitoring_tasks[user_id]
            task.cancel()
            del monitoring_tasks[user_id]
        if user_id in user_ssh_clients:
            user_ssh_clients[user_id].close()
            del user_ssh_clients[user_id]
            await message.answer("Отключение от сервера выполнено.")
        else:
            await message.answer("Соединение не установлено.")

    @router.message(Command(commands=['add_monitoring']))
    async def set_monitoring_path(message: types.Message, state: FSMContext):
        user_id = message.from_user.id
        if user_id not in user_ssh_clients:
            await message.answer("Сначала подключитесь к серверу.")
            return
        await state.set_state(CommandState.setting_monitoring_path)
        await message.answer("Пожалуйста, введите полный путь к файлу для мониторинга:")

    @router.message(CommandState.setting_monitoring_path)
    async def process_monitoring_path(message: types.Message, state: FSMContext):
        SUPPORTED_FORMATS = ['.csv', '.json', '.log', '.txt']
        monitoring_path = message.text
        user_id = message.from_user.id
        if user_id not in saved_connection_details:
            saved_connection_details[user_id] = {}
        saved_connection_details[user_id]['monitoring_path'] = monitoring_path

        file_extension = os.path.splitext(monitoring_path)[1]
        if file_extension.lower() not in SUPPORTED_FORMATS:
            await message.answer(
                "Формат файла не поддерживается, поддерживаемые форматы: " + ', '.join(SUPPORTED_FORMATS))
        else:
            await state.clear()
            await message.answer(
                f"Путь для мониторинга установлен: {monitoring_path}")
            metrics = get_metrics(user_id, monitoring_path, bot, user_ssh_clients)
            await state.update_data(available_metrics=metrics)
            await message.answer(
                f"Доступные метрики: {', '.join(metrics)}\nВведите метрики, которые вы хотите отобразить, разделенные запятыми (например, 'Value1, Value2').")
            await state.set_state(CommandState.awaiting_metric_selection)


    @router.message(CommandState.awaiting_metric_selection)
    async def process_metrics(message: types.Message, state: FSMContext):
        user_data = await state.get_data()
        available_metrics = user_data['available_metrics']
        selected_metrics = message.text.split(',')
        selected_metrics = [metric.strip() for metric in selected_metrics if metric.strip() in available_metrics]

        if not selected_metrics:
            await message.answer("Не выбраны действительные метрики. Пожалуйста, попробуйте снова.")
            return
        await state.clear()
        plot_configurations = user_data.get('plot_configurations', [])
        plot_configurations.append(selected_metrics)
        await state.update_data(plot_configurations=plot_configurations)
        await state.update_data(available_metrics=available_metrics)
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Да", callback_data="add_more_yes"),
             InlineKeyboardButton(text="Нет", callback_data="add_more_no")]
        ])
        await message.answer("Настройка одного графика завершена. Хотите добавить еще одну конфигурацию? (да/нет)",
                             reply_markup=markup)

    @router.callback_query(F.data == 'add_more_yes')
    async def handle_add_more(callback_query: types.CallbackQuery, state: FSMContext):
        await callback_query.message.edit_text("Введите другой набор метрик для графика, разделенных запятыми.")
        await state.set_state(CommandState.awaiting_metric_selection)

    @router.callback_query(F.data == 'add_more_no')
    async def handle_no_more(callback_query: types.CallbackQuery, state: FSMContext):
        user_id = callback_query.from_user.id
        user_data = await state.get_data()
        monitoring_path = saved_connection_details[user_id]['monitoring_path']
        await callback_query.message.edit_text("Настройка графика завершена.")
        metrics = user_data.get('plot_configurations', [])
        task = asyncio.create_task(monitor_file(user_id, monitoring_path, bot, user_ssh_clients, metrics))
        task_id = id(task)
        if user_id not in monitoring_tasks.keys():
            monitoring_tasks[user_id] = {}
        monitoring_tasks[user_id][task_id] = task
        await bot.send_message(user_id, f"Мониторинг начат для файла {monitoring_path}. ID задачи: {task_id}")

    @router.message(Command(commands=['stop_monitoring']))
    async def stop_monitoring(message: types.Message):
        user_id = message.from_user.id
        if user_id in monitoring_tasks and monitoring_tasks[user_id]:
            kb = []
            for task_id, task in monitoring_tasks[user_id].items():
                kb.append([InlineKeyboardButton(text=f"Остановить задачу {task_id}", callback_data=f"stop_{task_id}")])
            kb.append([InlineKeyboardButton(text=f"Остановить все задачи", callback_data=f"st_all")])
            markup = InlineKeyboardMarkup(inline_keyboard=kb)
            await message.answer("Выберите задачу мониторинга для остановки:", reply_markup=markup)
        else:
            await message.answer("Активные задачи мониторинга не найдены.")

    @router.callback_query(F.data.startswith('stop_'))
    async def stop_selected_monitoring(callback_query: CallbackQuery):
        task_id = int(callback_query.data.split('_')[1])
        user_id = callback_query.from_user.id
        if task_id in monitoring_tasks[user_id]:
            monitoring_tasks[user_id][task_id].cancel()
            del monitoring_tasks[user_id][task_id]
            await callback_query.message.answer(f"Задача мониторинга {task_id} остановлена.")
            await callback_query.message.edit_reply_markup()  # Optional: Remove the inline buttons
        else:
            await callback_query.message.answer("Задача не найдена или уже остановлена.")

    @router.callback_query(F.data.startswith('st_all'))
    async def stop_selected_monitoring(callback_query: CallbackQuery):
        user_id = callback_query.from_user.id
        for task_id in monitoring_tasks[user_id]:
            monitoring_tasks[user_id][task_id].cancel()
            del monitoring_tasks[user_id][task_id]
        await callback_query.message.answer(f"Задачи мониторинга остановлены.")
        await callback_query.message.edit_reply_markup()  # Optional: Remove the inline buttons

    @router.message(Command(commands=['cancel_job']))
    async def cancel_job_command(message: types.Message):
        user_id = message.from_user.id
        if user_id not in user_ssh_clients:
            await message.answer("Сначала подключитесь к серверу.")
            return
        ssh_client = user_ssh_clients[user_id]
        job_id = message.text.split(' ')[1] if len(message.text.split(' ')) > 1 else None
        if job_id:
            response = await cancel_job(ssh_client, job_id)
            await message.answer(response)
        else:
            await message.answer("Пожалуйста, укажите ID задачи. Формат: /cancel_job [job_id]")

    @router.message(Command(commands=['show_queue']))
    async def show_queue_command(message: types.Message):
        user_id = message.from_user.id
        if user_id not in user_ssh_clients:
            await message.answer("Сначала подключитесь к серверу.")
            return
        ssh_client = user_ssh_clients[user_id]
        response = await show_queue(ssh_client)
        max_length = 4000
        if len(response) > max_length:
            response = response[:max_length] + "\n... (output truncated)"
        await message.answer(response)

    @router.message(CommandState.waiting_for_job)
    async def process_submit_job_command(message: types.Message, state: FSMContext):
        await state.clear()
        job_script_path = message.text
        user_id = message.from_user.id
        ssh_client = user_ssh_clients[user_id]
        response = await submit_job(ssh_client, job_script_path)
        await message.answer(response)

    @router.message(Command(commands=['submit_job']))
    async def submit_job_command(message: types.Message, state: FSMContext):
        user_id = message.from_user.id
        if user_id not in user_ssh_clients:
            await message.answer("Сначала подключитесь к серверу.")
            return
        ssh_client = user_ssh_clients[user_id]
        await state.set_state(CommandState.waiting_for_job)
        await message.answer("Введите путь к скрипту задачи на сервере:")

    @router.message(CommandState.waiting_for_download_filename)
    async def process_download_file_command(message: types.Message, state: FSMContext):
        await state.clear()
        remote_file_path = message.text
        local_file_path = f'./downloads/{remote_file_path.split("/")[-1]}'

        os.makedirs('./downloads', exist_ok=True)

        user_id = message.from_user.id

        ssh_client = user_ssh_clients[user_id]
        response = await download_file(ssh_client, remote_file_path, local_file_path)
        if "успешно" in response:
            document = FSInputFile(local_file_path)
            await bot.send_document(message.from_user.id, document=document)
        else:
            await message.answer(response)

    @router.message(Command(commands=['download']))
    async def download_file_command(message: types.Message, state: FSMContext):
        user_id = message.from_user.id
        if user_id not in user_ssh_clients:
            await message.answer("Сначала подключитесь к серверу.")
            return
        ssh_client = user_ssh_clients[user_id]
        await state.set_state(CommandState.waiting_for_download_filename)
        await message.answer("Введите путь к файлу на сервере, который хотите скачать:")

    @router.message(Command(commands=['upload']))
    async def upload_file_command(message: types.Message, state: FSMContext):
        user_id = message.from_user.id
        if user_id not in user_ssh_clients:
            await message.answer("Сначала подключитесь к серверу.")
            return
        await state.set_state(CommandState.waiting_for_upload_file)
        await message.answer("Отправьте файл который хотите загрузить")

    @router.message(CommandState.waiting_for_upload_file)
    async def process_upload_file_command(message: Message, state: FSMContext):
        try:
            document = message.document
            file_id = document.file_id
            file_name = document.file_name
            file_path = f'./downloads/{file_name}'

            logging.info(f"Received document with file_id: {file_id}")
            logging.info(f"Local file path: {file_path}")
        except Exception as e:
            response = f"Произошла ошибка: {e}"
            logging.error(response)

            await message.answer("ошибка загрузки файла, попробуйте еще раз")

        try:
            state.clear()
            os.makedirs('./downloads', exist_ok=True)
            logging.info(f"Ensured that the downloads directory exists.")

            file = await bot.get_file(file_id)
            file_path_telegram = file.file_path
            logging.info(f"File path on Telegram servers: {file_path_telegram}")

            await bot.download_file(file_path_telegram, destination=file_path)
            logging.info(f"File downloaded locally to {file_path}")

            if os.path.exists(file_path):
                user_id = message.from_user.id
                ssh_client = user_ssh_clients[user_id]
                logging.info(f"File {file_name} exists, ready to upload.")
                remote_path = f'{file_name}'  # Modify as needed
                response = await upload_file(ssh_client, file_path, remote_path)
                logging.info(f"File uploaded to remote server at path: {remote_path}")
            else:
                response = f"Ошибка: Файл {file_name} не найден локально после загрузки."
                logging.error(response)

        except Exception as e:
            response = f"Произошла ошибка: {e}"
            logging.error(response)

        await message.answer(response)

    @router.message(Command(commands=['execute']))
    async def execute_command(message: types.Message, state: FSMContext):
        user_id = message.from_user.id
        if user_id not in user_ssh_clients:
            await message.answer("Сначала подключитесь к серверу.")
            return
        ssh_client = user_ssh_clients[user_id]
        await state.set_state(CommandState.waiting_for_command)
        await message.answer("Введите команду для выполнения:")

    @router.message(CommandState.waiting_for_command)
    async def process_execute_command(message: types.Message, state: FSMContext):
        if message.text.startswith('/'):
            await message.answer(
                "Похоже, вы ввели команду бота. Если вы хотите выполнить команду SSH, пожалуйста, не начинайте с '/'.")
            return

        command = message.text
        await state.clear()

        try:
            user_id = message.from_user.id
            ssh_client = user_ssh_clients[user_id]
            stdin, stdout, stderr = ssh_client.exec_command(command)
            output = stdout.read().decode('utf-8').strip()
            error = stderr.read().decode('utf-8').strip()
            response = output or error or "Нет вывода"
            await message.answer(response)
        except Exception as e:
            await message.answer(f"Ошибка выполнения команды: {e}")

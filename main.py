import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler
)
import logging
from io import BytesIO
from datetime import datetime

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


SEARCH, COMPARE_FIRST, COMPARE_SECOND = range(3)

MOLECULE_EXAMPLES = {
    "Лекарства": [
        ("Аспирин", "aspirin"),
        ("Ибупрофен", "ibuprofen"),
        ("Парацетамол", "paracetamol")
    ],
    "Аминокислоты": [
        ("Глицин", "glycine"),
        ("Аланин", "alanine")
    ],
    "Витамины": [
        ("Витамин C", "ascorbic acid"),
        ("Витамин B12", "vitamin b12")
    ]
}

SEARCH_HISTORY = {}
FAVORITES = {}

def create_main_menu():
    """Создает клавиатуру главного меню"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Поиск молекулы", callback_data='search')],
        [InlineKeyboardButton("🎲 Случайная молекула", callback_data='random')],
        [InlineKeyboardButton("⚖️ Сравнить молекулы", callback_data='compare')],
        [InlineKeyboardButton("📚 Примеры молекул", callback_data='examples')],
        [InlineKeyboardButton("📋 История поиска", callback_data='history'),
         InlineKeyboardButton("⭐ Избранное", callback_data='favorites')],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data='help')]
    ])

class PubChemClient:
    BASE_URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
    
    @classmethod
    def search_compound(cls, query, by_cid=False):
        try:
            if by_cid:
                cid = query
            else:
                search_url = f"{cls.BASE_URL}/compound/name/{query}/cids/JSON"
                response = requests.get(search_url, timeout=10)
                if response.status_code != 200:
                    return None
                cid = response.json()['IdentifierList']['CID'][0]
            
            record_url = f"{cls.BASE_URL}/compound/cid/{cid}/JSON"
            response = requests.get(record_url, timeout=10)
            if response.status_code != 200:
                return None
                
            data = response.json()
            compound = data['PC_Compounds'][0]
            
            props = {p['urn']['label']: p['value']['sval'] 
                    for p in compound.get('props', []) 
                    if 'urn' in p and 'label' in p['urn'] and 'sval' in p['value']}
            
            return {
                'CID': cid,
                'Name': query if not by_cid else props.get('IUPAC Name', f"CID {cid}"),
                'MolecularFormula': compound.get('atoms', {}).get('fstring', 'N/A'),
                'MolecularWeight': compound.get('coords', [{}])[0].get('weight', {}).get('value', 'N/A'),
                'IUPACName': props.get('IUPAC Name', 'N/A'),
                'CanonicalSMILES': props.get('SMILES', 'N/A'),
                'InChIKey': props.get('InChIKey', 'N/A'),
                'image_url': f"{cls.BASE_URL}/compound/cid/{cid}/PNG",
                'pubchem_url': f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}"
            }
        except Exception as e:
            logger.error(f"PubChem API error: {e}")
            return None
    
    @classmethod
    def get_random_compound(cls):
        try:
            response = requests.get(
                f"{cls.BASE_URL}/compound/random/random.cid/JSON",
                timeout=10
            )
            if response.status_code == 200:
                cid = response.json()['IdentifierList']['CID'][0]
                return cls.search_compound(cid, by_cid=True)
        except Exception as e:
            logger.error(f"Error getting random compound: {e}")
        return None
    
    @classmethod
    def get_similar_compounds(cls, cid, limit=5):
        try:
            url = f"{cls.BASE_URL}/compound/fastsimilarity_2d/cid/{cid}/cids/JSON?Threshold=90&MaxRecords={limit}"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                return response.json()['IdentifierList']['CID']
        except Exception as e:
            logger.error(f"Error getting similar compounds: {e}")
        return []

async def safe_edit_or_reply(callback_query, text, reply_markup=None, parse_mode=None):
    try:
        await callback_query.edit_message_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    except Exception as e:
        logger.warning(f"Couldn't edit message, sending new: {e}")
        await callback_query.message.reply_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )

async def show_main_menu(update: Update, text: str = "👨‍🔬 *Химический бот*\n\nВыберите действие:"):
    """Показывает главное меню"""
    if update.callback_query:
        await safe_edit_or_reply(
            update.callback_query,
            text,
            reply_markup=create_main_menu(),
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=create_main_menu(),
            parse_mode='Markdown'
        )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    await show_main_menu(update)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает справку"""
    help_text = (
        "🆘 *Помощь*\n\n"
        "Этот бот помогает искать информацию о химических соединениях в базе PubChem.\n\n"
        "🔹 *Основные функции:*\n"
        "- Поиск по названию или CID\n"
        "- Просмотр случайных молекул\n"
        "- Сравнение свойств молекул\n"
        "- История поиска и избранное\n\n"
        "Используйте кнопки меню для навигации."
    )
    await safe_edit_or_reply(
        update.callback_query,
        help_text,
        reply_markup=create_main_menu(),
        parse_mode='Markdown'
    )

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает процесс поиска"""
    await safe_edit_or_reply(
        update.callback_query,
        "🔍 Введите название молекулы или её CID:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("↩️ Назад", callback_data='back_to_menu')]
        ])
    )
    return SEARCH

async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает введенный поисковый запрос"""
    await process_chemical_search(update.message, update.message.text)
    return ConversationHandler.END

async def random_molecule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает случайную молекулу"""
    result = PubChemClient.get_random_compound()
    if result:
        await send_molecule_info(update.callback_query.message, result)
    else:
        await show_main_menu(update, "Не удалось получить случайную молекулу.")

async def compare_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает процесс сравнения"""
    await safe_edit_or_reply(
        update.callback_query,
        "⚖️ Введите название или CID первой молекулы:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("↩️ Назад", callback_data='back_to_menu')]
        ])
    )
    return COMPARE_FIRST

async def compare_first(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает первую молекулу для сравнения"""
    result = PubChemClient.search_compound(update.message.text)
    if result:
        context.user_data['compare_first'] = result
        await update.message.reply_text(
            "Теперь введите вторую молекулу:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ Назад", callback_data='back_to_menu')]
            ])
        )
        return COMPARE_SECOND
    else:
        await update.message.reply_text(
            "Молекула не найдена. Попробуйте снова:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ Назад", callback_data='back_to_menu')]
            ])
        )
        return COMPARE_FIRST

async def compare_second(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает вторую молекулу и показывает сравнение"""
    second = PubChemClient.search_compound(update.message.text)
    if second:
        first = context.user_data['compare_first']
        await send_comparison(update.message, first, second)
    else:
        await update.message.reply_text(
            "Молекула не найдена. Попробуйте снова:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ Назад", callback_data='back_to_menu')]
            ])
        )
        return COMPARE_SECOND
    return ConversationHandler.END

async def send_comparison(message, first, second):
    """Отправляет результат сравнения"""
    try:
        mw1 = float(first['MolecularWeight']) if first['MolecularWeight'] != 'N/A' else 0
        mw2 = float(second['MolecularWeight']) if second['MolecularWeight'] != 'N/A' else 0
        mass_diff = abs(mw1 - mw2)
        
        text = (
            "⚖️ *Сравнение молекул*\n\n"
            f"1️⃣ *{first['Name']}*\n"
            f"• Формула: `{first['MolecularFormula']}`\n"
            f"• Масса: `{first['MolecularWeight']}`\n\n"
            f"2️⃣ *{second['Name']}*\n"
            f"• Формула: `{second['MolecularFormula']}`\n"
            f"• Масса: `{second['MolecularWeight']}`\n\n"
            f"📊 Разница масс: `{mass_diff:.2f}`"
        )
        
        await message.reply_text(
            text,
            reply_markup=create_main_menu(),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Comparison error: {e}")
        await message.reply_text(
            "Ошибка при сравнении.",
            reply_markup=create_main_menu()
        )

async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает историю поиска"""
    user_id = update.callback_query.from_user.id
    if user_id not in SEARCH_HISTORY or not SEARCH_HISTORY[user_id]:
        await show_main_menu(update, "История поиска пуста.")
        return
    
    history = SEARCH_HISTORY[user_id][-5:]  # Последние 5 запросов
    keyboard = [
        [InlineKeyboardButton(
            f"{item['name']}", 
            callback_data=f"history_{item['cid']}")]
        for item in reversed(history)
    ]
    keyboard.append([InlineKeyboardButton("↩️ Назад", callback_data='back_to_menu')])
    
    await safe_edit_or_reply(
        update.callback_query,
        "🔍 История поиска:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает избранные молекулы"""
    user_id = update.callback_query.from_user.id
    if user_id not in FAVORITES or not FAVORITES[user_id]:
        await show_main_menu(update, "У вас нет сохранённых молекул.")
        return
    
    keyboard = [
        [InlineKeyboardButton(name, callback_data=f"history_{cid}"),
         InlineKeyboardButton("❌", callback_data=f"remove_{cid}")]
        for cid, name in FAVORITES[user_id].items()
    ]
    keyboard.append([InlineKeyboardButton("↩️ Назад", callback_data='back_to_menu')])
    
    await safe_edit_or_reply(
        update.callback_query,
        "⭐ Избранные молекулы:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_examples(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает категории примеров"""
    keyboard = [
        [InlineKeyboardButton(category, callback_data=f"category_{category}")]
        for category in MOLECULE_EXAMPLES.keys()
    ]
    keyboard.append([InlineKeyboardButton("↩️ Назад", callback_data='back_to_menu')])
    
    await safe_edit_or_reply(
        update.callback_query,
        "📚 Примеры молекул:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает молекулы в выбранной категории"""
    category = update.callback_query.data.replace("category_", "")
    examples = MOLECULE_EXAMPLES.get(category, [])
    
    keyboard = [
        [InlineKeyboardButton(name, callback_data=f"search_{term}")]
        for name, term in examples
    ]
    keyboard.append([InlineKeyboardButton("↩️ Назад", callback_data='examples')])
    
    await safe_edit_or_reply(
        update.callback_query,
        f"🔬 {category}:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def process_chemical_search(message, query, by_cid=False):
    """Обрабатывает поиск химического соединения"""
    result = PubChemClient.search_compound(query, by_cid)
    if not result:
        await message.reply_text(
            f"Не удалось найти молекулу '{query}'.",
            reply_markup=create_main_menu()
        )
        return
    
    # Сохраняем в историю
    user_id = message.from_user.id
    if user_id not in SEARCH_HISTORY:
        SEARCH_HISTORY[user_id] = []
    
    SEARCH_HISTORY[user_id].append({
        'name': result['Name'],
        'cid': result['CID'],
        'time': datetime.now().timestamp()
    })
    
    await send_molecule_info(message, result)

async def send_molecule_info(message, data):
    """Отправляет информацию о молекуле"""
    try:
        response = requests.get(data['image_url'], timeout=10)
        if response.status_code == 200:
            photo = BytesIO(response.content)
            photo.name = 'molecule.png'
            
            keyboard = [
                [
                    InlineKeyboardButton("📊 PubChem", url=data['pubchem_url']),
                    InlineKeyboardButton("🧪 Похожие", callback_data=f"similar_{data['CID']}")
                ],
                [
                    InlineKeyboardButton("💾 Сохранить", callback_data=f"save_{data['CID']}"),
                    InlineKeyboardButton("↩️ Меню", callback_data='back_to_menu')
                ]
            ]
            
            await message.reply_photo(
                photo=photo,
                caption=format_molecule_info(data),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        else:
            await message.reply_text(
                format_molecule_info(data),
                reply_markup=create_main_menu(),
                parse_mode='Markdown'
            )
    except Exception as e:
        logger.error(f"Error sending molecule info: {e}")
        await message.reply_text(
            format_molecule_info(data),
            reply_markup=create_main_menu(),
            parse_mode='Markdown'
        )

def format_molecule_info(data):
    """Форматирует информацию о молекуле"""
    return (
        f"🔬 *{data['Name']}*\n\n"
        f"• 📊 CID: `{data['CID']}`\n"
        f"• 🧪 Формула: `{data['MolecularFormula']}`\n"
        f"• ⚖️ Масса: `{data['MolecularWeight']}`\n"
        f"• 📝 IUPAC: `{data['IUPACName']}`\n"
        f"• 🔠 SMILES: `{data['CanonicalSMILES']}`\n"
        f"• 🔑 InChIKey: `{data['InChIKey']}`"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает текстовые сообщения"""
    await process_chemical_search(update.message, update.message.text)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатия на кнопки"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'back_to_menu':
        await show_main_menu(update)
    elif query.data == 'search':
        await search_command(update, context)
    elif query.data == 'random':
        await random_molecule(update, context)
    elif query.data == 'compare':
        await compare_command(update, context)
    elif query.data == 'examples':
        await show_examples(update, context)
    elif query.data == 'history':
        await show_history(update, context)
    elif query.data == 'favorites':
        await show_favorites(update, context)
    elif query.data == 'help':
        await help_command(update, context)
    elif query.data.startswith('category_'):
        await show_category(update, context)
    elif query.data.startswith('search_'):
        await process_chemical_search(query.message, query.data.replace("search_", ""))
    elif query.data.startswith('history_'):
        await process_chemical_search(query.message, query.data.replace("history_", ""), True)
    elif query.data.startswith('similar_'):
        cid = query.data.replace("similar_", "")
        similar = PubChemClient.get_similar_compounds(cid)
        if similar:
            keyboard = [
                [InlineKeyboardButton(f"Молекула {i+1}", callback_data=f"history_{cid}")]
                for i, cid in enumerate(similar)
            ]
            keyboard.append([InlineKeyboardButton("↩️ Назад", callback_data=f"history_{query.data.replace('similar_', '')}")])
            await safe_edit_or_reply(
                query,
                "🧪 Похожие молекулы:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    elif query.data.startswith('save_'):
        cid = query.data.replace("save_", "")
        user_id = query.from_user.id
        if user_id not in FAVORITES:
            FAVORITES[user_id] = {}
        result = PubChemClient.search_compound(cid, True)
        if result:
            FAVORITES[user_id][cid] = result['Name']
            await query.answer(f"Сохранено: {result['Name']}")
    elif query.data.startswith('remove_'):
        cid = query.data.replace("remove_", "")
        user_id = query.from_user.id
        if user_id in FAVORITES and cid in FAVORITES[user_id]:
            name = FAVORITES[user_id].pop(cid)
            await query.answer(f"Удалено: {name}")
            await show_favorites(update, context)

def main():
    """Запускает бота"""
    application = Application.builder().token("7606289346:AAF0mbAJpbssJEtocPcXmPELcZqXYbXcVbs").build()
    
    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    
    # Обработчики диалогов
    search_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(search_command, pattern='^search$')],
        states={
            SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search)]
        },
        fallbacks=[CallbackQueryHandler(show_main_menu, pattern='^back_to_menu$')]
    )
    
    compare_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(compare_command, pattern='^compare$')],
        states={
            COMPARE_FIRST: [MessageHandler(filters.TEXT & ~filters.COMMAND, compare_first)],
            COMPARE_SECOND: [MessageHandler(filters.TEXT & ~filters.COMMAND, compare_second)]
        },
        fallbacks=[CallbackQueryHandler(show_main_menu, pattern='^back_to_menu$')]
    )
    
    application.add_handler(search_conv)
    application.add_handler(compare_conv)
    
    # Обработчик обычных сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Обработчик кнопок
    application.add_handler(CallbackQueryHandler(button_handler))
    
    application.run_polling()

if __name__ == '__main__':
    main()
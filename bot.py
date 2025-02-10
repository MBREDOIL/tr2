import logging
from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler
import requests
import hashlib
import json
import os
from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from datetime import datetime

# लॉगिंग सेटअप
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

USER_DATA_FILE = 'user_data.json'

def get_domain(url):
    """URL से डोमेन निकालें"""
    parsed_uri = urlparse(url)
    return f"{parsed_uri.netloc}"

def load_user_data():
    """यूज़र डेटा लोड करें"""
    try:
        with open(USER_DATA_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_user_data(user_data):
    """यूज़र डेटा सेव करें"""
    with open(USER_DATA_FILE, 'w') as f:
        json.dump(user_data, f, indent=4)

def fetch_url_content(url):
    """वेबसाइट कंटेंट फ़ेच करें"""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.text
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return None

def extract_documents(html_content, base_url):
    """HTML से डॉक्यूमेंट लिंक निकालें"""
    soup = BeautifulSoup(html_content, 'lxml')
    document_extensions = ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt']
    documents = []
    
    for link in soup.find_all('a', href=True):
        href = link['href']
        absolute_url = urljoin(base_url, href)
        link_text = link.text.strip()
        
        if any(absolute_url.lower().endswith(ext) for ext in document_extensions):
            # लिंक टेक्स्ट या फ़ाइलनाम का उपयोग करें
            if not link_text:
                filename = os.path.basename(absolute_url)
                link_text = os.path.splitext(filename)[0]
            
            documents.append({
                'name': link_text,
                'url': absolute_url
            })
    
    # डुप्लीकेट हटाएं
    return list({doc['url']: doc for doc in documents}.values())

async def create_document_file(url, documents):
    """डॉक्यूमेंट्स की TXT फ़ाइल बनाएं"""
    domain = get_domain(url)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{domain}_documents_{timestamp}.txt"
    
    with open(filename, 'w', encoding='utf-8') as f:
        for doc in documents:
            f.write(f"{doc['name']}\n{doc['url']}\n\n")
    
    return filename

async def check_website_updates(client):
    """वेबसाइट अपडेट चेक करें"""
    user_data = load_user_data()
    for user_id, data in user_data.items():
        for url_info in data['tracked_urls']:
            url = url_info['url']
            stored_hash = url_info['hash']
            stored_documents = url_info['documents']

            current_content = fetch_url_content(url)
            if not current_content:
                continue

            current_hash = hashlib.sha256(current_content.encode()).hexdigest()
            current_documents = extract_documents(current_content, url)

            if current_hash != stored_hash:
                try:
                    # सामान्य बदलाव सूचना
                    await client.send_message(
                        chat_id=user_id,
                        text=f"🚨 वेबसाइट में बदलाव आया है! {url}"
                    )
                except Exception as e:
                    logger.error(f"Error sending update to {user_id}: {e}")

                # नए डॉक्यूमेंट चेक करें
                new_docs = [doc for doc in current_documents 
                           if doc not in stored_documents]
                
                if new_docs:
                    try:
                        # TXT फ़ाइल बनाकर भेजें
                        txt_file = await create_document_file(url, new_docs)
                        await client.send_document(
                            chat_id=user_id,
                            document=txt_file,
                            caption=f"📄 {url} पर नए डॉक्यूमेंट मिले ({len(new_docs)})"
                        )
                        os.remove(txt_file)
                    except Exception as e:
                        logger.error(f"Error sending document to {user_id}: {e}")

                    # डेटा अपडेट करें
                    url_info['documents'] = current_documents
                    url_info['hash'] = current_hash
    
    save_user_data(user_data)

async def start(client, message):
    """स्टार्ट कमांड हैंडलर"""
    await message.reply_text(
        'वेबसाइट ट्रैकिंग बॉट में आपका स्वागत है!\n\n'
        'कमांड्स:\n'
        '/track <url> - वेबसाइट ट्रैक करें\n'
        '/untrack <url> - ट्रैकिंग रोकें\n'
        '/list - ट्रैक की गई वेबसाइट्स देखें\n'
        '/documents <url> - डॉक्यूमेंट्स की सूची प्राप्त करें'
    )

async def track(client, message):
    """URL ट्रैक करें"""
    user_id = str(message.from_user.id)
    url = ' '.join(message.command[1:]).strip()

    if not url.startswith(('http://', 'https://')):
        await message.reply_text("⚠ कृपया वैध URL डालें (http/https के साथ)")
        return

    user_data = load_user_data()
    if user_id not in user_data:
        user_data[user_id] = {'tracked_urls': []}

    if any(u['url'] == url for u in user_data[user_id]['tracked_urls']):
        await message.reply_text("❌ यह URL पहले से ट्रैक किया जा रहा है")
        return

    content = fetch_url_content(url)
    if not content:
        await message.reply_text("❌ URL एक्सेस नहीं किया जा सका")
        return

    current_hash = hashlib.sha256(content.encode()).hexdigest()
    current_documents = extract_documents(content, url)

    user_data[user_id]['tracked_urls'].append({
        'url': url,
        'hash': current_hash,
        'documents': current_documents
    })

    save_user_data(user_data)
    await message.reply_text(f"✅ ट्रैकिंग शुरू: {url}\nमिले डॉक्यूमेंट्स: {len(current_documents)}")

async def untrack(client, message):
    """ट्रैकिंग रोकें"""
    user_id = str(message.from_user.id)
    url = ' '.join(message.command[1:]).strip()

    user_data = load_user_data()
    if user_id not in user_data:
        await message.reply_text("❌ कोई ट्रैक किए गए URL नहीं मिले")
        return

    original_count = len(user_data[user_id]['tracked_urls'])
    user_data[user_id]['tracked_urls'] = [
        u for u in user_data[user_id]['tracked_urls']
        if u['url'] != url
    ]

    if len(user_data[user_id]['tracked_urls']) < original_count:
        save_user_data(user_data)
        await message.reply_text(f"❎ ट्रैकिंग बंद: {url}")
    else:
        await message.reply_text("❌ URL नहीं मिला")

async def list_urls(client, message):
    """ट्रैक किए गए URLs दिखाएं"""
    user_id = str(message.from_user.id)
    user_data = load_user_data()

    if user_id not in user_data or not user_data[user_id]['tracked_urls']:
        await message.reply_text("📭 आपने अभी कोई URL ट्रैक नहीं किया है")
        return

    urls = "\n".join([u['url'] for u in user_data[user_id]['tracked_urls']])
    await message.reply_text(f"📜 ट्रैक किए गए URLs:\n\n{urls}")

async def list_documents(client, message):
    """डॉक्यूमेंट्स की सूची भेजें"""
    user_id = str(message.from_user.id)
    url = ' '.join(message.command[1:]).strip()

    user_data = load_user_data()
    if user_id not in user_data or not user_data[user_id]['tracked_urls']:
        await message.reply_text("❌ आपने कोई URL ट्रैक नहीं किया है")
        return

    url_info = next((u for u in user_data[user_id]['tracked_urls'] if u['url'] == url), None)
    if not url_info:
        await message.reply_text("❌️ यह URL ट्रैक नहीं किया गया है")
        return

    documents = url_info.get('documents', [])
    if not documents:
        await message.reply_text(f"ℹ️ {url} पर कोई डॉक्यूमेंट नहीं मिला")
    else:
        try:
            txt_file = await create_document_file(url, documents)
            await client.send_document(
                chat_id=user_id,
                document=txt_file,
                caption=f"📑 {url} के सभी डॉक्यूमेंट्स ({len(documents)})"
            )
            os.remove(txt_file)
        except Exception as e:
            logger.error(f"Error sending documents list: {e}")
            await message.reply_text("❌ डॉक्यूमेंट्स भेजने में त्रुटि")

def main():
    """मुख्य एप्लिकेशन"""
    app = Client(
        "my_bot",
        api_id="YOUR_API_ID",
        api_hash="YOUR_API_HASH",
        bot_token="YOUR_BOT_TOKEN"
    )

    # कमांड हैंडलर्स
    handlers = [
        MessageHandler(start, filters.command("start")),
        MessageHandler(track, filters.command("track")),
        MessageHandler(untrack, filters.command("untrack")),
        MessageHandler(list_urls, filters.command("list")),
        MessageHandler(list_documents, filters.command("documents"))
    ]
    
    for handler in handlers:
        app.add_handler(handler)

    # शेड्यूलर सेटअप
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_website_updates, 'interval', minutes=30, args=[app])
    scheduler.start()

    try:
        app.run()
    except Exception as e:
        logger.error(f"बॉट चलाने में त्रुटि: {e}")

if __name__ == '__main__':
    main()

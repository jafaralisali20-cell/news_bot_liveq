import os
import re
import hashlib
import io
import asyncio
import aiohttp
import feedparser
from deep_translator import GoogleTranslator
from aiohttp import web
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display

BOT_TOKEN     = "8602549699:AAEOrF-CnILqUSLlOi-6DHf9amrVaAYjsu8"
TARGET        = "@WorldNewsLi"
POLL_INTERVAL = 30 # seconds

_seen = set()
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# --- SOURCES ---
RSS_FEEDS = {
      "\u0627\u0644\u062c\u0632\u064a\u0631\u0629 \u0639\u0627\u062c\u0644": "https://www.aljazeera.net/aljazeerarss/a7c3d207-1647-498b-90e6-69d67a149c71/7a4239e3-861f-442a-9e8c-f05256e6d1fb",
      "\u0627\u0644\u062c\u0632\u064a\u0631\u0629 \u0627\u0644\u0625\u062e\u0628\u0627\u0631\u064a\u0629": "https://www.aljazeera.net/aljazeerarss/1e4f76ae-3f5a-4674-8e5d-a3db53bc48ac/a9d60f7c-06bb-4b4c-ba2f-476fd81f4ba8",
      "\u0627\u0644\u0639\u0631\u0628\u064a\u0629": "https://www.alarabiya.net/.mrss/ar/last-24-hours.xml",
      "\u0627\u0644\u062d\u062f\u062b": "https://www.alarabiya.net/.mrss/ar/alhadeeth.xml",
      "\u0628\u064a \u0628\u064a \u0633\u064a": "https://feeds.bbci.co.uk/arabic/rss.xml",
      "\u0633\u064a \u0625\u0646 \u0625\u0646": "https://arabic.cnn.com/rss/cnnarabic_world.rss",
      "\u0633\u0643\u0627\u064a \u0646\u064a\u0648\u0632": "https://www.skynewsarabia.com/rss/feeds/rss.xml",
      "\u0631\u0648\u0633\u064a\u0627 \u0627\u0644\u064a\u0648\u0645": "https://arabic.rt.com/rss/",
      "\u0641\u0631\u0627\u0646\u0633 24": "https://www.france24.com/ar/rss",
      "\u0631\u0648\u064a\u062a\u0631\u0632": "https://feeds.reuters.com/reuters/topNews",
      "\u0623\u0633\u0648\u0634\u064a\u062a\u062f \u0628\u0631\u0633": "https://feeds.apnews.com/apf-intlnews"
}


# --- UTILS ---
def clean_text(text: str) -> str:
      if not text: return ""
            text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'www\.\S+', '', text)
    text = re.sub(r't\.me/\S+', '', text)
    text = re.sub(r'@\w+', '', text)
    return re.sub(r'\s+', ' ', text).strip()

def make_hash(text: str) -> str:
      return hashlib.md5(text.encode()).hexdigest()

def is_short(text: str, min_chars: int = 30) -> bool:
      return len(text.strip()) < min_chars

def rephrase_news(title: str, summary: str) -> str:
      full_text = f"{title}. {summary}" if summary and summary != title else title
    sentences = [s.strip() for s in full_text.split('.') if len(s.strip()) > 15]
    if len(sentences) == 0: return title
          bullet = "\U0001f539"
    rephrased = f"{bullet} <b>{sentences[0]}</b>\n\n"
    if len(sentences) > 1:
              rephrased += "<b>\u0623\u0628\u0631\u0632 \u0627\u0644\u062a\u0641\u0627\u0635\u064a\u0644:</b>\n"
              for s in sentences[1:3]:
                            rephrased += f"\u2022 {s}\n"
                    return rephrased.strip()

async def download_font():
      font_path = "Cairo-Bold.ttf"
    if not os.path.exists(font_path):
              url = "https://github.com/google/fonts/raw/main/ofl/cairo/Cairo-Bold.ttf"
        async with aiohttp.ClientSession() as session:
                      async with session.get(url) as r:
                                        if r.status == 200:
                                                              with open(font_path, "wb") as f:
                                                                                        f.write(await r.read())
                                                                    return font_path

async def download_image(url: str) -> io.BytesIO:
      async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as r:
                              if r.status == 200:
                                                data = await r.read()
                                                return io.BytesIO(data)
                                    return None

def draw_news_banner(image_bytes: io.BytesIO, is_urgent: bool = False) -> io.BytesIO:
      try:
                img = Image.open(image_bytes).convert("RGB")
                width, height = img.size
                banner_height = int(height * 0.15) if height > 400 else 60
                draw = ImageDraw.Draw(img)
                color = (200, 30, 30) if is_urgent else (20, 80, 200)
                draw.rectangle([(0, 0), (width, banner_height)], fill=color)

        raw_text = "\u0639\u0640\u0627\u062C\u0640\u0644" if is_urgent else "\u0645\u0640\u0647\u0640\u0645"
        reshaped_text = arabic_reshaper.reshape(raw_text)
        bidi_text = get_display(reshaped_text)
        font_path = "Cairo-Bold.ttf"
        font_size = int(banner_height * 0.7)
        try:
                      font = ImageFont.truetype(font_path, font_size)
except IOError:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), bidi_text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (width - text_w) / 2
        y = (banner_height - text_h) / 2 - (font_size * 0.2)
        draw.text((x, y), bidi_text, font=font, fill=(255, 255, 255))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85)
        out.seek(0)
        return out
except Exception as e:
        print(f"[IMG ERR] {e}")
        image_bytes.seek(0)
        return image_bytes

# --- MAIN ---
def translate(text: str, dest: str) -> str:
      try:
                return GoogleTranslator(source="auto", target=dest).translate(text) or text
except Exception:
        return text

async def send_photo_message(session: aiohttp.ClientSession, text: str, image_bytes) -> bool:
      try:
                data = aiohttp.FormData()
                data.add_field("chat_id", TARGET)
                data.add_field("caption", text[:1024])
                data.add_field("parse_mode", "HTML")
                data.add_field("photo", image_bytes, filename="news.jpg", content_type="image/jpeg")
                async with session.post(f"{TG_API}/sendPhoto", data=data, timeout=20) as r:
                              res = await r.json()
                              return res.get("ok", False)
      except Exception as e:
        print(f"[SEND ERR] {e}")
        return False

BREAKING_KW = ["\u0639\u0627\u062c\u0644", "breaking", "urgent", "exclusive", "\u062d\u0635\u0631\u064a", "\u0642\u0635\u0641", "\u0627\u0646\u0641\u062c\u0627\u0631"]
def is_urgent(text: str) -> bool:
      t = text.lower()
    return any(k in t for k in BREAKING_KW)

def extract_image_url(entry) -> str:
      if 'media_content' in entry and len(entry.media_content) > 0:
                return entry.media_content[0].get('url', "")
            if 'media_thumbnail' in entry and len(entry.media_thumbnail) > 0:
                      return entry.media_thumbnail[0].get('url', "")
                  if 'links' in entry:
                            for link in entry.links:
                                          if link.get('type', '').startswith('image/'):
                                                            return link.get('href', "")
                                                return ""

async def poll_all(session: aiohttp.ClientSession) -> None:
      for source_name, url in RSS_FEEDS.items():
                try:
                              async with session.get(url, timeout=12) as resp:
                                                if resp.status != 200: continue
                                                                  raw = await resp.text(errors="replace")
                                                feed = feedparser.parse(raw)
                                                for entry in feed.entries[:3]:
                                                                      title = clean_text(getattr(entry, "title", ""))
                                                                      summary = clean_text(getattr(entry, "summary", ""))
                                                                      full = f"{title}. {summary}" if summary and summary != title else title
                                                                      if is_short(full): continue
                                                                                            h = make_hash(full)
                                                                      if h in _seen: continue
                                                                                            _seen.add(h)
                                                                      if len(_seen) > 5000: _seen.clear()
                                                                                            rephrased_ar = rephrase_news(title, summary)
                                                                      en_title = translate(title, "en")
                                                                      urgent = is_urgent(full)
                                                                      badge = "\U0001f6a8 \u0639\u0627\u062c\u0644 | BREAKING" if urgent else "\U0001f4f0 \u0623\u062e\u0628\u0627\u0631 | News"
                                                                      img_url = extract_image_url(entry)
                                                                      img_bytes = None
                                                                      if img_url:
                                                                                                raw_img = await download_image(img_url)
                                                                                                if raw_img:
                                                                                                                              img_bytes = draw_news_banner(raw_img, is_urgent=urgent)
                                                                                                                      caption = (
                                                                                                f"{badge}\n\n"
                                                                                                f"{rephrased_ar}\n\n"
                                                                                                f"\U0001f1fa\U0001f1f8 {en_title}\n\n"
                                                                                                f"\U0001f4e1 \u0627\u0644\u0645\u0635\u062f\u0631: {source_name}\n"
                                                                                                f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                                                                                                f"\U0001f30d \u0627\u0644\u0623\u062e\u0628\u0627\u0631 \u0627\u0644\u0639\u0627\u0644\u0645\u064a\u0629 | World News\n"
                                                                                                f"\U0001f517 t.me/{TARGET.replace('@', '')}"
                                                                                                )
                                                                                            if img_bytes:
                                                                                                                      await send_photo_message(session, caption, img_bytes)
                                                  else:
                        payload = {"chat_id": TARGET, "text": caption, "parse_mode": "HTML"}
                        async with session.post(f"{TG_API}/sendMessage", json=payload) as fallback_r:
                                                      await fallback_r.read()
                                              await asyncio.sleep(1.5)
except Exception:
            pass

async def polling_loop() -> None:
      connector = aiohttp.TCPConnector(limit=20)
    async with aiohttp.ClientSession(connector=connector) as session:
              await download_font()
        while True:
                      await poll_all(session)
            await asyncio.sleep(POLL_INTERVAL)

async def health(_):
      return web.Response(text="Bot Running")

async def start_server() -> None:
      app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()

async def main() -> None:
      await asyncio.gather(start_server(), polling_loop())

if __name__ == "__main__":
      async def run_main():
                try:
                              await main()
except Exception as e:
            print(f"[CRITICAL ERR] {e}")
            await asyncio.sleep(10)
            await run_main()
    asyncio.run(run_main())

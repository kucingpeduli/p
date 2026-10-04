"""
bot.py - Main Discord Bot Entry Point (Gemini AI + Auto Message Engine)
Bot Discord Cerdas untuk Analisis Pasar Saham, Laporan Keuangan, dan Berita Katalis Berbasis Google Gemini AI
Dilengkapi Sistem Pengiriman Pesan Otomatis (Auto Message / Alert Scheduler).
"""

import os
import sys
import json
import asyncio
from datetime import datetime
import pytz
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

# Atur encoding UTF-8 untuk output terminal Windows agar tidak terjadi UnicodeEncodeError
if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

def safe_log(text: str):
    """Mencetak log yang aman dari error encoding terminal Windows."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="replace").decode("ascii"))

from stock_engine import StockEngine, format_currency, format_percent
from ai_engine import AIAnalyst

# Muat variabel environment
load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "alert_config.json")

# Inisialisasi Intents Discord
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
stock_engine = StockEngine()
ai_analyst = AIAnalyst()


# =====================================================================
# MANAJEMEN KONFIGURASI AUTO-MESSAGE
# =====================================================================
def load_config() -> dict:
    default_config = {
        "enabled": True,
        "channel_id": None,
        "timezone": "Asia/Jakarta",
        "morning_time": "08:30",
        "afternoon_time": "16:30",
        "market": "BOTH",  # Pilihan: 'IDX', 'US', 'BOTH'
        "last_morning_sent": None,
        "last_afternoon_sent": None
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
                default_config.update(saved)
        except Exception as e:
            safe_log(f"[Warning] Gagal membaca config: {e}")
    return default_config


def save_config(cfg: dict):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        safe_log(f"[Error] Gagal menyimpan config: {e}")


class ActionButtons(discord.ui.View):
    """Tombol interaktif di bawah embed Discord."""
    def __init__(self, news_links=None):
        super().__init__(timeout=180)
        if news_links:
            for idx, item in enumerate(news_links[:2]):
                if item.get("link"):
                    self.add_item(discord.ui.Button(
                        label=f"📰 {item['publisher'][:18]}",
                        url=item["link"],
                        style=discord.ButtonStyle.link
                    ))


def split_text_chunks(text: str, max_chunk_size: int = 1900):
    """Membagi teks panjang agar tidak melampaui limit karakter Discord (2000 chars)."""
    chunks = []
    while len(text) > max_chunk_size:
        split_index = text.rfind("\n\n", 0, max_chunk_size)
        if split_index == -1:
            split_index = text.rfind("\n", 0, max_chunk_size)
        if split_index == -1:
            split_index = max_chunk_size
        chunks.append(text[:split_index].strip())
        text = text[split_index:].strip()
    if text:
        chunks.append(text)
    return chunks


# =====================================================================
# FUNGSI EKSEKUSI AUTO-MESSAGE SAHAM
# =====================================================================
async def send_auto_market_broadcast(channel: discord.TextChannel, session_type: str = "MORNING", market_type: str = "BOTH"):
    """
    Mengirimkan pesan otomatis berisi saham berpotensi tinggi,
    analisis laporan keuangan, dan rangkuman katalis berita oleh Gemini AI.
    """
    is_morning = session_type.upper() == "MORNING"
    session_title = "🌅 [PRE-MARKET BRIEFING] Sesi Pagi" if is_morning else "🌆 [MARKET CLOSING WRAP] Sesi Penutupan"
    embed_color = discord.Color.gold() if is_morning else discord.Color.dark_teal()

    markets_to_process = []
    if market_type in ["IDX", "BOTH"]:
        markets_to_process.append(("IDX", "Bursa Efek Indonesia (IDX / IHSG)"))
    if market_type in ["US", "BOTH"]:
        markets_to_process.append(("US", "Bursa Wall Street (US / NYSE & NASDAQ)"))

    for code, m_name in markets_to_process:
        top_stocks = await stock_engine.get_top_potential_stocks(code)
        if not top_stocks:
            continue

        embed = discord.Embed(
            title=f"{session_title} - {m_name}",
            description=(
                f"Berikut adalah saham dengan potensi kenaikan dan momentum terbaik hari ini "
                f"berdasarkan algoritma kuantitatif & laporan keuangan terkini:"
            ),
            color=embed_color,
            timestamp=datetime.utcnow()
        )

        for idx, s in enumerate(top_stocks[:4], 1):
            sign = "+" if s["pct_change"] >= 0 else ""
            pe_str = f"{s['pe']:.1f}x" if isinstance(s.get("pe"), (int, float)) and s["pe"] else "N/A"
            embed.add_field(
                name=f"#{idx} {s['symbol']} - {s['name']}",
                value=(
                    f"💵 **Harga:** {s['currency']} {s['price']:,.2f} ({sign}{s['pct_change']:.2f}%)\n"
                    f"⭐ **Skor Potensi:** `{s['potential_score']}/100`\n"
                    f"📊 **P/E:** {pe_str} | **ROE:** {format_percent(s['roe'])} | **Rev Growth:** {format_percent(s['revenue_growth'])}\n"
                    f"🏢 **Sektor:** {s['sector']}"
                ),
                inline=False
            )

        embed.set_footer(text="Dianalisis secara otomatis oleh Google Gemini AI & yfinance")
        await channel.send(embed=embed)

        # Generate ulasan mendalam & katalis berita dari Gemini AI
        ai_brief = await ai_analyst.generate_auto_market_report(top_stocks, session_type, m_name)
        chunks = split_text_chunks(ai_brief)
        for chunk in chunks:
            await channel.send(chunk)


# =====================================================================
# EVENT ON_READY & BACKGROUND SCHEDULER
# =====================================================================
@bot.event
async def on_ready():
    safe_log("==================================================")
    safe_log(f"[INFO] Bot Berhasil Login: {bot.user.name} ({bot.user.id})")
    safe_log("[INFO] Powered by Google Gemini AI & yfinance")
    safe_log("==================================================")

    try:
        synced = await bot.tree.sync()
        safe_log(f"[SUCCESS] Berhasil menyinkronkan {len(synced)} Slash Commands.")
    except Exception as e:
        safe_log(f"[ERROR] Gagal sinkronisasi Slash Commands: {e}")

    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="Pasar Saham & Auto Alert | /bantuan"
        )
    )

    if not auto_alert_scheduler_task.is_running():
        auto_alert_scheduler_task.start()
        safe_log("[INFO] Scheduler Auto-Message Saham aktif (Mengecek setiap 1 menit).")


@tasks.loop(minutes=1)
async def auto_alert_scheduler_task():
    """Tugas terjadwal di background yang mengecek jam setiap menit untuk mengirim auto-message."""
    await bot.wait_until_ready()
    cfg = load_config()

    if not cfg.get("enabled") or not cfg.get("channel_id"):
        return

    tz_name = cfg.get("timezone", "Asia/Jakarta")
    try:
        tz = pytz.timezone(tz_name)
    except Exception:
        tz = pytz.timezone("Asia/Jakarta")

    now = datetime.now(tz)
    current_time_str = now.strftime("%H:%M")
    today_date_str = now.strftime("%Y-%m-%d")

    channel = bot.get_channel(int(cfg["channel_id"]))
    if not channel:
        return

    # 1. Cek Sesi Pagi (Pre-Market Opening Briefing)
    if current_time_str == cfg.get("morning_time", "08:30") and cfg.get("last_morning_sent") != today_date_str:
        safe_log(f"[Auto Message] Mengirimkan Briefing Sesi Pagi ke channel #{channel.name}...")
        cfg["last_morning_sent"] = today_date_str
        save_config(cfg)
        try:
            await send_auto_market_broadcast(channel, session_type="MORNING", market_type=cfg.get("market", "BOTH"))
        except Exception as e:
            safe_log(f"[ERROR] Error saat mengirim broadcast pagi: {e}")

    # 2. Cek Sesi Sore (Market Closing Wrap)
    if current_time_str == cfg.get("afternoon_time", "16:30") and cfg.get("last_afternoon_sent") != today_date_str:
        safe_log(f"[Auto Message] Mengirimkan Wrap Penutupan Sesi Sore ke channel #{channel.name}...")
        cfg["last_afternoon_sent"] = today_date_str
        save_config(cfg)
        try:
            await send_auto_market_broadcast(channel, session_type="AFTERNOON", market_type=cfg.get("market", "BOTH"))
        except Exception as e:
            safe_log(f"[ERROR] Error saat mengirim broadcast sore: {e}")


@auto_alert_scheduler_task.before_loop
async def before_scheduler():
    await bot.wait_until_ready()


# =====================================================================
# SLASH COMMANDS: AUTO-MESSAGE MANAGEMENT
# =====================================================================

@bot.tree.command(name="atur_pesan_otomatis", description="Atur pengiriman pesan otomatis (auto-message) info saham harian.")
@app_commands.describe(
    channel="Channel Discord tujuan pengiriman pesan",
    jam_pagi="Jam pengiriman sesi pagi format HH:MM (Default: 08:30 WIB)",
    jam_sore="Jam pengiriman sesi sore format HH:MM (Default: 16:30 WIB)",
    pasar="Pasar saham yang dipantau (IDX, US, atau Keduanya)",
    status="Aktifkan atau nonaktifkan pesan otomatis"
)
@app_commands.choices(pasar=[
    app_commands.Choice(name="Keduanya (Indonesia IDX & Amerika US)", value="BOTH"),
    app_commands.Choice(name="Hanya Bursa Indonesia (IDX / IHSG)", value="IDX"),
    app_commands.Choice(name="Hanya Bursa Amerika (US / Wall Street)", value="US")
])
@app_commands.checks.has_permissions(administrator=True)
async def slash_atur_pesan_otomatis(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    jam_pagi: str = "08:30",
    jam_sore: str = "16:30",
    pasar: app_commands.Choice[str] = None,
    status: bool = True
):
    cfg = load_config()
    cfg["enabled"] = status
    cfg["channel_id"] = channel.id
    cfg["morning_time"] = jam_pagi.strip()
    cfg["afternoon_time"] = jam_sore.strip()
    cfg["market"] = pasar.value if pasar else "BOTH"
    save_config(cfg)

    status_str = "🟢 **AKTIF**" if status else "🔴 **NONAKTIF**"
    embed = discord.Embed(
        title="⚙️ Konfigurasi Pesan Otomatis Saham Berhasil Disimpan",
        color=discord.Color.green(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="📢 Channel Target", value=channel.mention, inline=True)
    embed.add_field(name="📶 Status Auto-Alert", value=status_str, inline=True)
    embed.add_field(name="🌍 Pasar Terpilih", value=cfg["market"], inline=True)
    embed.add_field(name="🌅 Sesi Pagi (Pre-Market)", value=f"`{cfg['morning_time']}` WIB", inline=True)
    embed.add_field(name="🌆 Sesi Sore (Closing)", value=f"`{cfg['afternoon_time']}` WIB", inline=True)
    embed.add_field(name="💡 Uji Coba", value="Gunakan `/kirim_pesan_sekarang` untuk langsung menguji tampilan pesan.", inline=False)

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="status_pesan_otomatis", description="Lihat status konfigurasi pesan otomatis (auto alert) saat ini.")
async def slash_status_pesan_otomatis(interaction: discord.Interaction):
    cfg = load_config()
    channel_mention = f"<#{cfg['channel_id']}>" if cfg.get("channel_id") else "*Belum ditentukan (Gunakan /atur_pesan_otomatis)*"
    status_icon = "🟢 AKTIF" if cfg.get("enabled") and cfg.get("channel_id") else "🔴 NONAKTIF / BELUM DIKONFIGURASI"

    embed = discord.Embed(
        title="📋 Status Pengiriman Pesan Otomatis Saham",
        color=discord.Color.blue(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Status", value=f"**{status_icon}**", inline=False)
    embed.add_field(name="Channel Target", value=channel_mention, inline=True)
    embed.add_field(name="Pasar", value=cfg.get("market", "BOTH"), inline=True)
    embed.add_field(name="Jadwal Pagi", value=f"`{cfg.get('morning_time', '08:30')}` WIB", inline=True)
    embed.add_field(name="Jadwal Sore", value=f"`{cfg.get('afternoon_time', '16:30')}` WIB", inline=True)
    embed.add_field(name="Terakhir Dikirim (Pagi)", value=str(cfg.get("last_morning_sent") or "-"), inline=True)
    embed.add_field(name="Terakhir Dikirim (Sore)", value=str(cfg.get("last_afternoon_sent") or "-"), inline=True)
    embed.set_footer(text="Gunakan /atur_pesan_otomatis untuk mengubah pengaturan")

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="kirim_pesan_sekarang", description="Kirim pesan otomatis informasi saham sekarang juga (Manual Test/Trigger).")
@app_commands.describe(
    sesi="Pilih jenis sesi pesan: Pagi (Pre-Market) atau Sore (Penutupan)",
    pasar="Pilih pasar yang ingin dikirimkan"
)
@app_commands.choices(
    sesi=[
        app_commands.Choice(name="Sesi Pagi (Pre-Market Briefing & Saham Potensial)", value="MORNING"),
        app_commands.Choice(name="Sesi Sore (Market Closing Wrap & Evaluasi)", value="AFTERNOON")
    ],
    pasar=[
        app_commands.Choice(name="Keduanya (Indonesia IDX & Amerika US)", value="BOTH"),
        app_commands.Choice(name="Bursa Indonesia (IDX)", value="IDX"),
        app_commands.Choice(name="Bursa Amerika (US)", value="US")
    ]
)
@app_commands.checks.has_permissions(administrator=True)
async def slash_kirim_pesan_sekarang(
    interaction: discord.Interaction, 
    sesi: app_commands.Choice[str] = None,
    pasar: app_commands.Choice[str] = None
):
    await interaction.response.defer(thinking=True)
    
    session_val = sesi.value if sesi else "MORNING"
    market_val = pasar.value if pasar else "BOTH"
    
    # Broadcast ke channel saat ini
    await interaction.followup.send(f"🚀 **Memproses dan mengirimkan Auto-Market Alert ({session_val} - {market_val})...**")
    await send_auto_market_broadcast(interaction.channel, session_type=session_val, market_type=market_val)


# =====================================================================
# SLASH COMMANDS: ANALISIS & SCREENING ON-DEMAND
# =====================================================================

@bot.tree.command(name="analisis", description="Analisis mendalam Gemini AI: Potensi saham, laporan keuangan, dan katalis berita.")
@app_commands.describe(ticker="Kode saham, contoh: NVDA, AAPL, BBCA.JK, BBRI.JK, ASII.JK")
async def slash_analisis(interaction: discord.Interaction, ticker: str):
    await interaction.response.defer(thinking=True)
    
    ticker_clean = ticker.strip().upper()
    data = await stock_engine.get_stock_analysis_data(ticker_clean)
    
    if not data:
        await interaction.followup.send(
            f"❌ **Simbol '{ticker_clean}' tidak ditemukan!**\n"
            f"💡 *Tips:* Saham Indonesia gunakan `.JK` (contoh: `BBCA.JK`, `BBRI.JK`). "
            f"Saham US ketik langsung kodenya (contoh: `NVDA`, `AAPL`, `TSLA`).",
            ephemeral=True
        )
        return

    ai_report = await ai_analyst.analyze_stock(data)
    embed_color = discord.Color.green() if data["price_change"] >= 0 else discord.Color.red()

    embed = discord.Embed(
        title=f"📈 Gemini AI Equity Report: {data['name']} ({data['symbol']})",
        description=f"**Sektor:** {data['sector']} | **Industri:** {data['industry']}\n"
                    f"**Negara:** {data['country']} | **Mata Uang:** {data['currency']}",
        color=embed_color,
        timestamp=datetime.utcnow()
    )

    curr = data["currency"]
    sign = "+" if data["price_change"] >= 0 else ""
    price_str = f"**{curr} {data['current_price']:,.2f}** ({sign}{data['price_change_pct']:.2f}%)"
    
    embed.add_field(name="💵 Harga Terakhir", value=price_str, inline=True)
    embed.add_field(name="🏛️ Market Cap", value=format_currency(data["market_cap"], curr), inline=True)
    embed.add_field(name="📊 Rentang 52 Minggu", value=f"{curr} {data['52_week_low']} - {data['52_week_high']}", inline=True)

    pe_str = f"{data['pe_trailing']:.2f}x" if data['pe_trailing'] else "N/A"
    pbv_str = f"{data['pbv']:.2f}x" if data['pbv'] else "N/A"
    roe_str = format_percent(data["roe"])
    rev_growth_str = format_percent(data["revenue_growth"])
    net_margin_str = format_percent(data["profit_margin"])
    fcf_str = format_currency(data["free_cash_flow"], curr)

    embed.add_field(name="📐 Valuasi (P/E | PBV)", value=f"{pe_str} | {pbv_str}", inline=True)
    embed.add_field(name="🎯 Profitabilitas (ROE)", value=roe_str, inline=True)
    embed.add_field(name="🚀 Pertumbuhan Omzet (YoY)", value=rev_growth_str, inline=True)
    embed.add_field(name="💰 Laba Bersih", value=format_currency(data["net_income"], curr), inline=True)
    embed.add_field(name="📊 Net Profit Margin", value=net_margin_str, inline=True)
    embed.add_field(name="🌊 Free Cash Flow", value=fcf_str, inline=True)

    if data["news"]:
        news_headlines = "\n".join([f"• [{n['publisher']}] {n['title'][:75]}..." for n in data["news"][:3]])
        embed.add_field(name="📰 Berita & Katalis Terkini", value=news_headlines, inline=False)

    embed.set_footer(text="Powered by Google Gemini AI & yfinance | Bukan Ajakan Finansial Mutlak")

    view = ActionButtons(news_links=data.get("news"))
    await interaction.followup.send(embed=embed, view=view)

    chunks = split_text_chunks(ai_report)
    for i, chunk in enumerate(chunks):
        if i == 0:
            await interaction.followup.send(f"🤖 **TELAAH FUNDAMENTAL & REKOMENDASI GEMINI AI:**\n\n{chunk}")
        else:
            await interaction.followup.send(chunk)


@bot.tree.command(name="top_picks", description="Skrining saham dengan potensi kenaikan tinggi hari ini (Momentum & Fundamental).")
@app_commands.describe(pasar="Pilih bursa saham: 'US' (Wall Street) atau 'IDX' (Indonesia)")
@app_commands.choices(pasar=[
    app_commands.Choice(name="Bursa Amerika (US / Wall Street)", value="US"),
    app_commands.Choice(name="Bursa Indonesia (IDX / IHSG)", value="IDX")
])
async def slash_top_picks(interaction: discord.Interaction, pasar: app_commands.Choice[str] = None):
    await interaction.response.defer(thinking=True)
    
    market_choice = pasar.value if pasar else "US"
    market_label = "Bursa Amerika (US)" if market_choice == "US" else "Bursa Indonesia (IDX)"
    
    top_stocks = await stock_engine.get_top_potential_stocks(market_choice)
    
    if not top_stocks:
        await interaction.followup.send("⚠️ Gagal memuat data skrining pasar saat ini. Silakan coba beberapa saat lagi.")
        return

    embed = discord.Embed(
        title=f"🏆 Top 5 Saham Berpotensi Tinggi Hari Ini ({market_label})",
        description="Hasil skrining algoritma berdasarkan momentum harga, pertumbuhan pendapatan, dan rasio ROE:",
        color=discord.Color.gold(),
        timestamp=datetime.utcnow()
    )

    for idx, s in enumerate(top_stocks, 1):
        sign = "+" if s["pct_change"] >= 0 else ""
        pe_str = f"{s['pe']:.1f}x" if isinstance(s.get("pe"), (int, float)) and s["pe"] else "N/A"
        field_value = (
            f"**Harga:** {s['currency']} {s['price']:,.2f} ({sign}{s['pct_change']:.2f}%)\n"
            f"**Skor Potensi AI:** ⭐ `{s['potential_score']}/100`\n"
            f"**ROE:** {format_percent(s['roe'])} | **YoY Growth:** {format_percent(s['revenue_growth'])} | **P/E:** {pe_str}\n"
            f"**Sektor:** {s['sector']}"
        )
        embed.add_field(name=f"#{idx} {s['symbol']} - {s['name']}", value=field_value, inline=False)

    embed.set_footer(text="Gunakan /analisis <ticker> untuk analisis lengkap Gemini")
    await interaction.followup.send(embed=embed)

    ai_brief = await ai_analyst.generate_auto_market_report(top_stocks, "MORNING", market_label)
    chunks = split_text_chunks(ai_brief)
    for chunk in chunks:
        await interaction.followup.send(chunk)


@bot.tree.command(name="berita", description="Analisis Gemini terhadap berita terbaru yang mempengaruhi kenaikan/penurunan saham.")
@app_commands.describe(ticker="Kode saham, misal NVDA, BBCA.JK, AAPL, AMMN.JK")
async def slash_berita(interaction: discord.Interaction, ticker: str):
    await interaction.response.defer(thinking=True)
    
    ticker_clean = ticker.strip().upper()
    data = await stock_engine.get_stock_analysis_data(ticker_clean)
    
    if not data:
        await interaction.followup.send(f"❌ Simbol '{ticker_clean}' tidak ditemukan.", ephemeral=True)
        return

    news_list = data.get("news", [])
    if not news_list:
        await interaction.followup.send(f"ℹ️ Belum ditemukan berita terkini untuk **{ticker_clean}**.")
        return

    ai_news_summary = await ai_analyst.summarize_news_catalysts(data["symbol"], data["name"], news_list)

    embed = discord.Embed(
        title=f"📰 Katalis Berita & Sentimen Pasar: {data['name']} ({data['symbol']})",
        color=discord.Color.blue(),
        timestamp=datetime.utcnow()
    )

    for n in news_list[:4]:
        link_str = f" [Buka Berita]({n['link']})" if n.get("link") else ""
        embed.add_field(
            name=f"{n['publisher']} ({n['date']})",
            value=f"{n['title']}{link_str}",
            inline=False
        )

    await interaction.followup.send(embed=embed)
    await interaction.followup.send(f"🧠 **ANALISIS SENTIMEN & KATALIS KENAIKAN OLEH GEMINI AI:**\n\n{ai_news_summary}")


@bot.tree.command(name="laporan", description="Bedah laporan keuangan mendalam perusahaan (Neraca, Laba Rugi, Valuasi).")
@app_commands.describe(ticker="Kode saham, misal BBCA.JK, TLKM.JK, MSFT, GOOGL")
async def slash_laporan(interaction: discord.Interaction, ticker: str):
    await interaction.response.defer(thinking=True)
    
    ticker_clean = ticker.strip().upper()
    data = await stock_engine.get_stock_analysis_data(ticker_clean)
    
    if not data:
        await interaction.followup.send(f"❌ Simbol '{ticker_clean}' tidak ditemukan.", ephemeral=True)
        return

    curr = data["currency"]
    embed = discord.Embed(
        title=f"📑 Ringkasan Laporan Keuangan: {data['name']} ({data['symbol']})",
        description=f"Laporan keuangan terbaru per data bursa resmi.",
        color=discord.Color.purple(),
        timestamp=datetime.utcnow()
    )

    embed.add_field(
        name="📊 Pendapatan & Profitabilitas",
        value=(
            f"• **Total Pendapatan (Revenue):** {format_currency(data['revenue'], curr)}\n"
            f"• **Pertumbuhan Revenue (YoY):** {format_percent(data['revenue_growth'])}\n"
            f"• **Laba Bersih (Net Income):** {format_currency(data['net_income'], curr)}\n"
            f"• **Gross Profit Margin:** {format_percent(data['gross_margin'])}\n"
            f"• **Operating Margin:** {format_percent(data['operating_margin'])}\n"
            f"• **Net Profit Margin:** {format_percent(data['profit_margin'])}"
        ),
        inline=False
    )

    embed.add_field(
        name="🏛️ Kesehatan Neraca & Kas",
        value=(
            f"• **Debt to Equity Ratio:** {data['debt_to_equity'] or 'N/A'}\n"
            f"• **Operating Cash Flow:** {format_currency(data['operating_cash_flow'], curr)}\n"
            f"• **Free Cash Flow:** {format_currency(data['free_cash_flow'], curr)}\n"
            f"• **Return on Equity (ROE):** {format_percent(data['roe'])}\n"
            f"• **Return on Assets (ROA):** {format_percent(data['roa'])}"
        ),
        inline=False
    )

    embed.add_field(
        name="🏷️ Rasio Valuasi Pasar",
        value=(
            f"• **P/E Trailing:** {data['pe_trailing'] or 'N/A'}\n"
            f"• **P/E Forward:** {data['pe_forward'] or 'N/A'}\n"
            f"• **Price to Book (PBV):** {data['pbv'] or 'N/A'}\n"
            f"• **Price to Sales (P/S):** {data['ps_ratio'] or 'N/A'}\n"
            f"• **Dividend Yield:** {format_percent(data['dividend_yield'])}"
        ),
        inline=False
    )

    embed.set_footer(text="Gunakan /analisis untuk membaca rekomendasi strategi dari Gemini")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="bantuan", description="Panduan lengkap penggunaan bot dan daftar perintah.")
async def slash_bantuan(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📚 Panduan Bot Saham Google Gemini AI",
        description="Bot ini dirancang untuk mendeteksi saham berpotensi tinggi, membedah laporan keuangan, menganalisis berita katalis kenaikan harga, dan mengirimkan pesan otomatis (auto-message).",
        color=discord.Color.teal()
    )
    embed.add_field(
        name="🔔 Fitur Pesan Otomatis (Auto Message)",
        value=(
            "• `/atur_pesan_otomatis` : Atur channel, jam pengiriman (Pagi/Sore), dan pasar pilihan.\n"
            "• `/status_pesan_otomatis` : Cek apakah pesan otomatis aktif, jadwal, dan channel tujuannya.\n"
            "• `/kirim_pesan_sekarang` : Uji coba kirim pesan alert otomatis saat ini juga (Admin)."
        ),
        inline=False
    )
    embed.add_field(
        name="📌 Perintah Analisis Mandiri",
        value=(
            "• `/analisis <ticker>` : Analisis komprehensif saham (Fundamental + Berita + Rekomendasi Gemini AI).\n"
            "• `/top_picks [pasar]` : Rekomendasi 5 saham berpotensi hari ini (Pilih US atau IDX).\n"
            "• `/berita <ticker>` : Analisis sentimen berita terkini & katalis penggerak harga.\n"
            "• `/laporan <ticker>` : Bedah detail metrik laporan keuangan & rasio valuasi."
        ),
        inline=False
    )
    embed.add_field(
        name="💡 Format Simbol Ticker",
        value=(
            "• **Saham Indonesia (IDX):** Tambahkan `.JK` di akhir kode (contoh: `BBCA.JK`, `BBRI.JK`, `ASII.JK`).\n"
            "• **Saham Amerika (US):** Ketik simbolnya langsung (contoh: `NVDA`, `AAPL`, `MSFT`, `TSLA`)."
        ),
        inline=False
    )
    embed.set_footer(text="Disclaimer: Analisis bersifat edukasi dan referensi, bukan jaminan keuntungan.")
    await interaction.response.send_message(embed=embed)


# =====================================================================
# RUNNER
# =====================================================================
if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN or DISCORD_BOT_TOKEN == "your_discord_bot_token_here":
        safe_log("[PERINGATAN] DISCORD_BOT_TOKEN belum diisi di file .env!")
    else:
        bot.run(DISCORD_BOT_TOKEN)

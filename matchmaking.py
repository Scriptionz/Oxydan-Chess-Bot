import time
import random
import itertools
import os
from datetime import datetime, timedelta

# ==========================================================
# ⚙️ MATCHMAKER AYARLARI (Buradan yönetebilirsin)
# ==========================================================
SETTINGS = {
    "RATED_MODE": False,          # True: Puanlı, False: Puansız (Test için False kalmalı)
    "MAX_PARALLEL_GAMES": 2,     # Aynı anda kaç maç yapılsın? (GitHub için 1 önerilir)
    "MIN_RATING": 2000,          # Rakip minimum kaç elo olsun?
    "MAX_RATING": 4000,          # Rakip maksimum kaç elo olsun?
    "SAFETY_LOCK_TIME": 60,      # Davet attıktan sonra kaç saniye dondurulsun? (Beton Fren)
    "STOP_FILE": "STOP.txt",     # Durdurma dosyası adı
    "TIME_CONTROLS": ["1+0", "1+1", "2+1",                  # Bullet
        "3+0", "3+2", "5+0", "5+3",            # Blitz
        "10+0", "10+5", "15+10",               # Rapid
        "30+0"], # Rastgele seçilecek süreler
    "POOL_REFRESH_SECONDS": 3600, # Bot listesi kaç saniyede bir güncellensin?
    "BLACKLIST_MINUTES": 60      # Reddeden veya maç yapılan botu kaç dk engelle?
}
# ==========================================================

class Matchmaker:
    def __init__(self, client, config, active_games): 
        self.client = client
        self.config = config.get("matchmaking", {})
        self.enabled = self.config.get("allow_feed", True)
        self.active_games = active_games  
        self.my_id = None
        self.bot_pool = []
        self.blacklist = {}
        self.last_pool_update = 0
        self._initialize_id()

    def _initialize_id(self):
        """Botun kendi ID'sini doğrular."""
        try:
            self.my_id = self.client.account.get()['id']
            print(f"[Matchmaker] Bağlantı Başarılı. ID: {self.my_id}")
        except: 
            self.my_id = "oxydan"

    def _refresh_bot_pool(self):
        """Online bot listesini çeker ve karıştırır."""
        now = time.time()
        if not self.bot_pool or (now - self.last_pool_update > SETTINGS["POOL_REFRESH_SECONDS"]):
            try:
                stream = self.client.bots.get_online_bots()
                online_bots = list(itertools.islice(stream, 50))
                self.bot_pool = [b.get('id') for b in online_bots if b.get('id') and b.get('id').lower() != self.my_id.lower()]
                random.shuffle(self.bot_pool)
                self.last_pool_update = now
                print(f"[Matchmaker] Bot havuzu güncellendi: {len(self.bot_pool)} bot bulundu.")
            except: 
                time.sleep(10)

    def _is_stop_triggered(self):
        """STOP.txt kontrolünü yapar."""
        stop_path = os.path.join(os.getcwd(), SETTINGS["STOP_FILE"])
        return os.path.exists(stop_path)

    def _find_suitable_target(self):
        """Ayarlara uygun rakibi seçer."""
        self._refresh_bot_pool()
        now = datetime.now()

        for candidate in self.bot_pool[:20]: # İlk 20 botu hızlıca tara
            if candidate in self.blacklist and self.blacklist[candidate] > now:
                continue
            
            try:
                user_data = self.client.users.get_public_data(candidate)
                perfs = user_data.get('perfs', {})
                # En yüksek rating hangisiyse onu baz al
                max_r = max([perfs.get(c, {}).get('rating', 0) for c in ['blitz', 'bullet', 'rapid']] or [0])

                if SETTINGS["MIN_RATING"] <= max_r <= SETTINGS["MAX_RATING"]:
                    return candidate
                else:
                    # Kriter dışı botu 12 saat engelle
                    self.blacklist[candidate] = now + timedelta(hours=12)
            except: 
                continue
        return None

    def start(self):
        if not self.enabled: return
        print(f"[Matchmaker] Sistem Aktif. (Rated: {SETTINGS['RATED_MODE']})")

        while True:
            # 1. STOP Kontrolü
            if self._is_stop_triggered():
                print(f"[Matchmaker] 🛑 {SETTINGS['STOP_FILE']} algılandı. Beklemede...")
                time.sleep(15)
                continue

            # 2. Maç Sayısı Kontrolü
            if len(self.active_games) >= SETTINGS["MAX_PARALLEL_GAMES"]:
                time.sleep(20)
                continue

            try:
                # 3. Rakip Bulma
                target = self._find_suitable_target()
                if not target:
                    time.sleep(30)
                    continue

                # 4. Süre Ayarları
                tc = random.choice(SETTINGS["TIME_CONTROLS"])
                t_limit, t_inc = map(int, tc.split('+'))

                # 5. Meydan Okuma
                print(f"[Matchmaker] -> {target} ({tc}) Davet ediliyor...")
                self.blacklist[target] = datetime.now() + timedelta(minutes=SETTINGS["BLACKLIST_MINUTES"])
                
                self.client.challenges.create(
                    username=target,
                    rated=SETTINGS["RATED_MODE"],
                    clock_limit=t_limit * 60,
                    clock_increment=t_inc
                )
                
                # 6. Güvenlik Kilidi (Beton Fren)
                print(f"[Matchmaker] ✅ Davet gitti. {SETTINGS['SAFETY_LOCK_TIME']}sn GÜVENLİK KİLİDİ aktif.")
                time.sleep(SETTINGS["SAFETY_LOCK_TIME"]) 

            except Exception as e:
                print(f"[Matchmaker] Hata: {e}")
                time.sleep(30)

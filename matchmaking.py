import time
import random
import itertools
from datetime import datetime, timedelta

class Matchmaker:
    def __init__(self, client, config, active_games): 
        self.client = client
        self.config_all = config
        self.config = config.get("matchmaking", {})
        self.enabled = self.config.get("allow_feed", True)
        self.active_games = active_games  
        self.my_id = None
        
        # Ayarlar
        self.min_rating = self.config.get("min_rating", 2000)
        self.max_rating = self.config.get("max_rating", 4000)
        self.max_parallel_games = 2 
        
        # Havuz ve Takip
        self.bot_pool = []
        self.blacklist = {} # {bot_id: expire_time}
        self.pending_challenges = {} # {target_id: sent_time} - Askıda kalanları temizlemek için
        self.last_pool_update = 0
        self.pool_timeout = 7200 
        self.consecutive_429s = 0

        self._initialize_id()

    def _initialize_id(self):
        while not self.my_id:
            try:
                self.my_id = self.client.account.get()['id']
                print(f"🤖 [Matchmaker] Kimlik Doğrulandı: {self.my_id}")
            except Exception:
                time.sleep(15)

    def _cleanup_expired_data(self):
        """Hafıza temizliği ve askıda kalan challenge kontrolü."""
        now = datetime.now()
        # Kara listeyi temizle
        self.blacklist = {k: v for k, v in self.blacklist.items() if v > now}
        
        # 5 dakikadan uzun süredir cevap gelmeyen challenge'ları iptal et (Slot açmak için)
        to_cancel = [tid for tid, sent_time in self.pending_challenges.items() 
                     if (now - sent_time).total_seconds() > 300]
        for target in to_cancel:
            print(f"🧹 [Matchmaker] Cevapsız meydan okuma temizlendi: {target}")
            del self.pending_challenges[target]

    def _refresh_bot_pool(self):
        now = time.time()
        if not self.bot_pool or (now - self.last_pool_update > self.pool_timeout):
            try:
                print("🔄 [Matchmaker] Bot havuzu tazeleniyor...")
                time.sleep(random.uniform(5, 10)) # API'ye nefes aldır
                stream = self.client.bots.get_online_bots()
                # Daha seçici havuz (Sadece aktif ve oynamaya hazır görünebilecek 60 bot)
                online_bots = list(itertools.islice(stream, 60))
                
                self.bot_pool = [b.get('id') for b in online_bots 
                                if b.get('id') and b.get('id').lower() != self.my_id.lower()]
                random.shuffle(self.bot_pool)
                self.last_pool_update = now
                self.consecutive_429s = 0 
            except Exception as e:
                self._handle_rate_limit(e)

    def _handle_rate_limit(self, error):
        self.consecutive_429s += 1
        wait_time = 1800 * self.consecutive_429s # 30dk, 60dk...
        print(f"🚨 [LICHESS BAN KORUMASI] 429 Alındı. {wait_time//60} dk tam sessizlik...")
        time.sleep(wait_time)

    def _is_bot_suitable(self, target_id):
        """Bug Koruması: Sadece puan değil, botun durumunu da derinlemesine inceler."""
        try:
            time.sleep(random.uniform(1.0, 2.5)) # İnsan taklidi gecikme
            user = self.client.users.get_public_data(target_id)
            
            # 1. Engel: Yasaklı veya kapalı hesap
            if user.get('tosViolation') or user.get('disabled') or user.get('closed'):
                return False
                
            # 2. Engel: Bot etiketi yoksa (Sadece botlarla oynamak güvenlidir)
            if user.get('title') != 'BOT':
                return False

            # 3. Engel: Rating Kontrolü
            perfs = user.get('perfs', {})
            # Sadece oynamak istediğimiz kategorilerin en yükseğine bak
            relevant_ratings = [p.get('rating') for k, p in perfs.items() 
                               if k in ['blitz', 'bullet', 'rapid'] and p.get('games', 0) > 20]
            
            if not relevant_ratings: return False
            best_rating = max(relevant_ratings)
            
            return self.min_rating <= best_rating <= self.max_rating
        except:
            return False

    def start(self):
        if not self.enabled: return
        print("🚀 [Matchmaker] Motor çalıştırıldı.")
        start_time = time.time()

        while True:
            try:
                self._cleanup_expired_data()
                
                # SLOT KONTROLÜ (Aktif maçlar + henüz kabul edilmemiş ama gönderilmiş olanlar)
                total_busy_slots = len(self.active_games) + len(self.pending_challenges)
                if total_busy_slots >= self.max_parallel_games:
                    time.sleep(30)
                    continue

                # ZAMAN KONTROLÜ
                elapsed = time.time() - start_time
                if elapsed > 21000: # 5 saat 50 dk
                    time.sleep(600)
                    continue

                self._refresh_bot_pool()
                
                # HEDEF SEÇİMİ
                target = None
                for potential in self.bot_pool:
                    if potential in self.blacklist or potential in self.pending_challenges:
                        continue
                    if self._is_bot_suitable(potential):
                        target = potential
                        break
                
                if not target:
                    time.sleep(60)
                    continue

                # ZAMAN KONTROLÜ VE TC SEÇİMİ
                dice = random.random()
                if elapsed > 18000: # Son saatlerde sadece hızlı maç
                    tc_list = ["1+0", "2+1", "3+0"]
                else:
                    if dice < 0.10: tc_list = ["10+0", "15+10"] # Klasik
                    elif dice < 0.30: tc_list = ["5+0", "5+3", "3+2"] # Blitz
                    else: tc_list = ["1+0", "2+1", "3+0"] # Bullet / SuperBlitz

                tc = random.choice(tc_list)
                limit, inc = map(int, tc.split('+'))

                # CHALLENGE GÖNDERİMİ
                try:
                    # Kara liste: Bu bota 2 saat boyunca bir daha sorma
                    self.blacklist[target] = datetime.now() + timedelta(hours=2)
                    self.pending_challenges[target] = datetime.now()

                    self.client.challenges.create(
                        username=target,
                        rated=True,
                        clock_limit=limit * 60,
                        clock_increment=inc
                    )
                    print(f"⚔️ [Challenge] -> {target} ({tc}) gönderildi.")
                    
                    # API Flood önleyici zorunlu bekleme
                    time.sleep(random.uniform(90, 150)) 

                except Exception as e:
                    if "429" in str(e):
                        self._handle_rate_limit(e)
                    else:
                        # Reddedildiyse listeden çıkar ama bekle
                        if target in self.pending_challenges: del self.pending_challenges[target]
                        time.sleep(30)

            except Exception as e:
                print(f"⚠️ [Matchmaker Döngü Hatası]: {e}")
                time.sleep(60)

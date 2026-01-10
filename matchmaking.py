import time
import random
import itertools

class Matchmaker:
    def __init__(self, client, config):
        self.client = client
        self.config = config.get("matchmaking", {})
        self.enabled = self.config.get("allow_feed", True)
        
        # Kendi ID'mizi doğru alalım
        try:
            self.my_id = self.client.account.get()['id']
            print(f"[Matchmaker] Kimlik: {self.my_id}")
        except:
            self.my_id = "oxydan"

    def start(self):
        print(f"[Matchmaker] Avcı modu aktif. Durum: {self.enabled}")
        if not self.enabled: return

        while True:
            try:
                # 1. Devam eden maç kontrolü
                ongoing = self.client.games.get_ongoing()
                if len(ongoing) >= self.config.get("max_games", 1):
                    time.sleep(30)
                    continue

                # 2. Rakip tara (Online botları her seferinde çekmek yerine listeyi al)
                print("[Matchmaker] Rakip listesi güncelleniyor...")
                online_bots_stream = self.client.bots.get_online_bots()
                online_bots = list(itertools.islice(online_bots_stream, 30)) # 50 yerine 30 yeterli
                
                target_bots = [b.get('id') for b in online_bots if b.get('id') and b.get('id').lower() != self.my_id.lower()]

                if target_bots:
                    # Rastgele 3 deneme yapalım (bazı botlar kapalı olabilir)
                    target = random.choice(target_bots)
                    tc = random.choice(self.config.get("time_controls", ["3+2"]))
                    t_limit, t_inc = map(int, tc.split('+'))
                    
                    print(f"[Matchmaker] Hedef: {target} ({tc}). Meydan okunuyor...")
                    self.client.challenges.create(
                        username=target,
                        rated=True,
                        clock_limit=t_limit * 60,
                        clock_increment=t_inc
                    )
                    print(f"[Matchmaker] İstek gönderildi: {target}")
                else:
                    print("[Matchmaker] Uygun rakip bulunamadı.")

                # Başarılı işlem sonrası bekleme
                interval = self.config.get("challenge_interval", 60)
                time.sleep(interval)

            except Exception as e:
                error_msg = str(e)
                if "429" in error_msg:
                    # Lichess bizi engellediyse uzun süre uyu
                    wait_time = 600 # 10 Dakika
                    print(f"[Matchmaker] KRİTİK: Lichess Hız Sınırı (429). {wait_time/60} dakika bekleniyor...")
                    time.sleep(wait_time)
                else:
                    print(f"[Matchmaker] Hata: {e}. 60sn sonra tekrar denenecek.")
                    time.sleep(60)

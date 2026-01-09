import time
import random

class Matchmaker:
    def __init__(self, client, config):
        self.client = client
        self.config = config.get("matchmaking", {})
        self.enabled = self.config.get("allow_feed", False)

    def start(self):
        if not self.enabled:
            return

        while True:
            try:
                # 1. Şu an bir oyunda mıyız kontrol et (Aynı anda tek maç)
                status = self.client.account.get_ongoing()
                if len(status) >= self.config.get("max_games", 1):
                    time.sleep(30) # Meşgulse bekle
                    continue

                # 2. Çevrimiçi botları bul
                print("[Matchmaker] Uygun rakipler aranıyor...")
                online_bots = self.client.bots.get_online_bots()
                
                # Kriterlere uygun botları filtrele
                target_bots = []
                for bot in online_bots:
                    # Kendi ismimizi eleyelim
                    if bot.get('id') == "oxydan": # Buraya kendi bot id'ni yazabilirsin
                        continue
                    
                    # Basit bir filtre: Sadece botları ve uygun zaman kontrolünü seç
                    target_bots.append(bot['id'])

                if target_bots:
                    target = random.choice(target_bots[:10]) # İlk 10 aktif bottan birini seç
                    time_control = random.choice(self.config.get("time_controls", ["3+2"]))
                    
                    # Zaman kontrolünü parçala (Örn: "3+2" -> 3 dk, 2 sn)
                    t_limit, t_increment = map(int, time_control.split('+'))
                    
                    print(f"[Matchmaker] {target} botuna meydan okunuyor ({time_control})...")
                    self.client.challenges.create(target, rated=True, clock_limit=t_limit*60, clock_increment=t_increment)
                
                # Bir sonraki arama için bekle
                time.sleep(self.config.get("challenge_interval", 30))

            except Exception as e:
                print(f"[Matchmaker] Hata oluştu: {e}")
                time.sleep(60)

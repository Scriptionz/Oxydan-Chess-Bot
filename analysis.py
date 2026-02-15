import requests
import json
from datetime import datetime

class OxydanAnalyzer:
    def __init__(self, username, token):
        self.username = username
        self.headers = {'Authorization': f'Bearer {token}'}
        self.stats_file = "oxydan_history.json"

    def run_daily_analysis(self):
        print(f"🚀 {self.username} için günlük analiz başlıyor...")
        
        # 100 oyunu analiz verileriyle çek (Lichess üzerinde analiz edilmişse)
        url = f"https://lichess.org/api/games/user/{self.username}"
        params = {
            'max': 100,
            'evals': 'true',
            'analysis': 'true',
            'accuracy': 'true'
        }
        
        response = requests.get(url, headers=self.headers, params=params)
        if response.status_code != 200:
            print("❌ Veri çekilemedi. API limiti veya Token sorunu.")
            return

        # PGN'leri parçala ve analiz et
        games = response.text.strip().split('\n\n\n')
        
        daily_report = {
            "date": str(datetime.now().date()),
            "total_games": len(games),
            "brilliant": 0,
            "blunder": 0,
            "inaccuracy": 0,
            "mistake": 0,
            "avg_accuracy": 0.0
        }

        for game in games:
            # Lichess analiz sembollerini sayar
            daily_report["brilliant"] += game.count("!!")
            daily_report["blunder"] += game.count("??")
            daily_report["mistake"] += game.count("?")
            daily_report["inaccuracy"] += game.count("?!")

        self.save_stats(daily_report)
        return daily_report

    def save_stats(self, report):
        try:
            with open(self.stats_file, 'r') as f:
                data = json.load(f)
        except FileNotFoundError:
            data = []
        
        data.append(report)
        with open(self.stats_file, 'w') as f:
            json.dump(data, f, indent=4)
        print("✅ Günlük rapor kaydedildi ve stats.json güncellendi.")

# Kullanım:
# analyzer = OxydanAnalyzer("OxydanBot", "TOKEN_BURAYA")
# analyzer.run_daily_analysis()

import json
import os

class ConfigManager:
    def __init__(self, config_file="config.json"):
        self.config_file = config_file
        self.sessions = {}
        self.snippets = {"System Info": "uname -a", "Disk Usage": "df -h"}
        self.language = "en"
        
        self.theme = {
            "mode": "dark",
            "accent": "#bd93f9",          
            "terminal_color": "#a6accd",  
            "bg_color": "#151015",        
            "layout": "classic",
            "term_style": "standard"
        }
        self.load_config()

    def load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r") as f:
                    data = json.load(f)
                    self.sessions = data.get("sessions", {})
                    self.snippets = data.get("snippets", self.snippets)
                    self.theme = data.get("theme", self.theme)
                    self.language = data.get("language", "en")
                    
                    if "layout" not in self.theme:
                        self.theme["layout"] = "classic"
                    if "bg_color" not in self.theme:
                        self.theme["bg_color"] = "#151015"
                    if "term_style" not in self.theme:
                        self.theme["term_style"] = "standard"
            except Exception:
                pass

    def save_config(self):
        try:
            with open(self.config_file, "w") as f:
                json.dump({
                    "sessions": self.sessions, 
                    "snippets": self.snippets,
                    "theme": self.theme,
                    "language": self.language
                }, f, indent=4)
        except Exception:
            pass

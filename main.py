import json
from typing import Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from ohbot import ohbot

# --- 1. Chargement de la configuration ---
def load_config():
    try:
        with open("config.json", "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        print("ATTENTION : Fichier config.json introuvable. Limites par défaut appliquées.")
        return {"motors": {
            "head_nod": {"id": 0, "name": "Inclinaison Tête", "min": 0, "max": 10},
            "head_turn": {"id": 1, "name": "Rotation Tête", "min": 0, "max": 10},
            "eye_turn": {"id": 2, "name": "Rotation Yeux", "min": 0, "max": 10},
            "lid_blink": {"id": 3, "name": "Paupières", "min": 0, "max": 10},
            "top_lip": {"id": 4, "name": "Lèvre Supérieure", "min": 0, "max": 10},
            "bottom_lip": {"id": 5, "name": "Lèvre Inférieure", "min": 0, "max": 10},
            "eye_tilt": {"id": 6, "name": "Inclinaison Yeux", "min": 0, "max": 10}
        }}

CONFIG = load_config()

# --- 2. Initialisation de l'API ---
app = FastAPI(title="Ohbot Sécurisé API")

@app.on_event("startup")
def startup_event():
    try:
        ohbot.reset()
        ohbot.say("Système en ligne et moteurs sécurisés.")
    except Exception as e:
        # Permet de lancer l'API même sans le robot branché pour tester
        print(f"Erreur d'initialisation Ohbot (êtes-vous sur le Pi avec Ohbot branché ?) : {e}")

class SpeechRequest(BaseModel):
    text: str

# --- 3. Les Routes ---

# Servir l'interface web à la racine du projet
@app.get("/")
def read_root():
    return FileResponse("index.html")

@app.post("/say")
def parler(request: SpeechRequest):
    try:
        ohbot.say(request.text)
        return {"status": "success", "message": f"Ohbot dit : {request.text}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/move/{motor_key}/{position}")
def bouger_moteur(motor_key: str, position: int):
    # On vérifie d'abord si le nom court (clé) existe dans notre configuration
    if motor_key not in CONFIG["motors"]:
        raise HTTPException(status_code=404, detail=f"Moteur '{motor_key}' inconnu.")

    # On récupère les informations pour ce moteur spécifique
    motor_info = CONFIG["motors"][motor_key]
    motor_id = motor_info["id"]
    min_pos = motor_info["min"]
    max_pos = motor_info["max"]
    motor_name = motor_info["name"]

    # Vérification de sécurité
    if position < min_pos or position > max_pos:
        raise HTTPException(
            status_code=400, 
            detail=f"DANGER: Position {position} refusée. "
                   f"Les limites pour '{motor_name}' sont [{min_pos} - {max_pos}]."
        )
    
    # Si tout est bon, on bouge le moteur
    try:
        ohbot.move(motor_id, position)
        ohbot.wait(0.1)
        return {
            "status": "success", 
            "motor": motor_name, 
            "position": position,
            "message": "Mouvement effectué avec succès"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur avec Ohbot : {e}")

@app.get("/config")
def get_config():
    return CONFIG

@app.post("/config")
def update_config(new_config: Dict[str, Any]):
    global CONFIG
    try:
        # Sauvegarde dans le fichier
        with open("config.json", "w", encoding="utf-8") as file:
            json.dump(new_config, file, indent=2, ensure_ascii=False)
        # Mise à jour en mémoire
        CONFIG = new_config
        return {"status": "success", "message": "Configuration mise à jour et sauvegardée"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/reset")
def reset_robot():
    try:
        ohbot.reset()
        ohbot.wait(0.2)
        return {"status": "success", "message": "Robot en position neutre"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/shutdown")
def eteindre():
    try:
        ohbot.reset()
        ohbot.close()
        return {"status": "success", "message": "Connexion fermée"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

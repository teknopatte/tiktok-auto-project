# TikTok Auto Project

Projet de base pour construire des outils d'automatisation autour de TikTok de facon propre, maintenable et conforme.

## Objectif

Créer une application qui aide a preparer, organiser, publier ou analyser du contenu TikTok sans recourir a des pratiques de spam, de contournement ou d'abus de plateforme.

## Etat du projet

- Initialisation du projet
- Base data: top 100 YouTube par niche
- Stack technique a confirmer
- Fonctionnalites a cadrer

## Principes

- Code lisible, teste et documente quand c'est utile
- Architecture simple au depart, extensible ensuite
- Secrets et identifiants jamais commits
- Automatisation responsable et conforme aux APIs/regles disponibles

## Prochaines decisions

- Choisir la stack: Python, Node.js, ou autre
- Definir la premiere fonctionnalite utile
- Connecter un depot GitHub distant

## Donnees

- `data/top_100_youtube_channels_by_niche.tsv`: top 100 YouTube classe par niche.
- `data/youtube_niche_summary.md`: synthese lisible par niche.
- `data/top_100_youtubeurs_fr_by_niche.tsv`: selection FR sans musiciens, classee par niche.
- `data/youtubeurs_fr_niche_summary.md`: synthese de la selection FR.

## Downloader YouTube recent

Le script `src/youtube_recent_downloader.py` verifie les dernieres videos des youtubeurs FR listes, telecharge les videos sorties recemment et les range par niche:

```powershell
python -m pip install -r requirements.txt
python src/youtube_recent_downloader.py --dry-run --limit 3
python src/youtube_recent_downloader.py
```

Par defaut, il stocke les videos dans `downloads/youtube/<niche>/<chaine>/` et garde un etat local dans `.state/youtube_recent_downloader.json` pour ne pas retelecharger deux fois la meme video.

Apres un vrai telechargement, la video est decoupee automatiquement en parties de 60 secondes dans `downloads/youtube/<niche>/<chaine>/clips/<video>/`. Le dernier clip contient le reste si la duree ne tombe pas pile.

Ensuite, chaque clip peut etre rendu en format vertical TikTok dans `downloads/youtube/<niche>/<chaine>/shorts/<video>/`: la moitie haute garde la video principale en horizontal avec un fond floute, et la moitie basse boucle une video satisfying locale choisie au hasard. Ajoute tes videos Trackmania/satisfying dans `videos_satisfaisantes/`.

Pour stocker dans un Drive, mets `VIDEO_OUTPUT_ROOT` dans `.env.example` / `.env` vers un dossier Drive synchronise localement, par exemple `G:\Mon Drive\tiktok-auto-project\youtube`.

## Application de controle

L'app locale donne un tableau de bord pour lancer les checks, suivre les logs, voir les chaines verifiees, les videos detectees et les telechargements.

```powershell
python src/control_app.py
```

Puis ouvre `http://127.0.0.1:8787`.

Le panneau `Auto runner` permet d'activer/desactiver une boucle automatique. Elle reutilise les reglages visibles dans le formulaire: mode `Test` pour surveiller sans telecharger, ou mode `Download` pour telecharger les nouvelles videos a chaque passage.

Les futures fonctionnalites sont listees dans `data/features.json`; l'interface les charge automatiquement depuis ce registre.

## Publication TikTok

La publication automatique passe uniquement par l'API officielle TikTok Content Posting.

Prerequis:

- app TikTok Developer enregistree
- produit Content Posting API active
- Login Kit active avec le redirect URI `https://tiktok.aemour.com/tiktok/callback/`
- scopes approuves selon le mode voulu (`user.info.basic`, `video.upload`, `video.list`, puis Direct Post si TikTok l'accorde)
- `TIKTOK_CLIENT_KEY` et `TIKTOK_CLIENT_SECRET` renseignes dans `.env`
- compte TikTok connecte depuis le dashboard local

Le dashboard expose un bouton `Connecter TikTok`. Le flux OAuth utilise l'URL HTTPS declaree dans TikTok Developer, puis la page publique `public/tiktok/callback/index.html` renvoie le code vers l'app locale `http://127.0.0.1:8787/tiktok/callback`.

Variables utiles:

```env
TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=
TIKTOK_REDIRECT_URI=https://tiktok.aemour.com/tiktok/callback/
TIKTOK_LOCAL_CALLBACK_URL=http://127.0.0.1:8787/tiktok/callback
TIKTOK_SCOPES=user.info.basic,video.upload,video.publish,video.list
```

Par securite, la privacy par defaut est `SELF_ONLY` et `TIKTOK_AUTO_PUBLISH=0`. Les tokens OAuth sont stockes localement dans `.state/tiktok_token.json` et ne doivent jamais etre commits.

Le mode manuel `Lien YouTube` utilise un pipeline optimise quand la publication TikTok est active: telechargement, puis pour chaque partie `clip -> rendu vertical -> publication`, au lieu d'attendre que tous les shorts soient generes. Le dashboard permet aussi de lancer un test sur un seul short, de regler le delai entre publications, et de nettoyer les fichiers d'un test echoue.

## Analyse objective des passages candidats

Le paquet `src.candidate_analysis` analyse une video locale sans creer physiquement les passages. Il execute une analyse globale unique, conserve les timelines, puis agrege les mesures sur toutes les fenetres demandees:

```text
video locale
  -> FFprobe (duree et presence audio)
  -> FFmpeg silencedetect (timeline des silences)
  -> Faster-Whisper local + VAD (segments de parole et mots horodates)
  -> cache global
  -> fenetres virtuelles
  -> six mesures brutes par fenetre
```

Il ne calcule aucun score, poids ou avis subjectif. Il n'est pas branche automatiquement sur la publication TikTok.

### Installation

FFmpeg et FFprobe doivent etre disponibles dans le `PATH`. Les dependances d'analyse sont separees du downloader pour ne pas alourdir son installation:

```powershell
python -m pip install -r requirements-analysis.txt
```

Faster-Whisper telecharge le modele open source demande lors de sa premiere utilisation si `--model` est un nom (`tiny`, `small`, etc.). Les utilisations suivantes sont locales. `--model` accepte aussi le chemin d'un modele deja present sur la machine. Aucune API IA payante et aucune cle ne sont utilisees.

### Commande

```powershell
python -m src.candidate_analysis analyze "video.mp4" --step 3 --durations 60,75,90,105,120 --output analysis.json
```

Options principales:

- `--silence-threshold-db -35` et `--minimum-silence-duration 0.25` configurent `silencedetect`;
- `--model small --language fr --device auto --compute-type default` configurent Faster-Whisper;
- `--hesitations euh,heu,hum,hmm,bah,ben` configure la liste explicite;
- `--cache-dir .cache/candidate_analysis` choisit le cache, et `--no-cache` le desactive.

Les valeurs par defaut des fenetres sont 60, 75, 90, 105 et 120 secondes avec un pas de 3 secondes.

### Definitions des six mesures

| Mesure | Definition et formule | Unite / plage |
|---|---|---|
| `silence_ratio` | Somme des intersections entre la fenetre et les intervalles `silencedetect`, divisee par la duree de la fenetre. | ratio `[0, 1]` |
| `longest_silence_seconds` | Duree de la plus longue intersection silencieuse continue dans la fenetre. Les intervalles qui se chevauchent sont fusionnes. | secondes `[0, duree]` |
| `speech_density` | Duree de l'union des segments de parole Faster-Whisper/VAD intersectant la fenetre, divisee par sa duree. Elle est calculee independamment de `silence_ratio`. | ratio `[0, 1]` |
| `words_per_minute` | Nombre de tokens dont le mot horodate commence dans la fenetre, divise par la duree de parole intersectee, multiplie par 60. Zero si aucune parole active. | mots/minute `>= 0` |
| `hesitation_ratio` | Nombre d'occurrences des mots ou expressions explicitement configures, divise par le nombre total de tokens de la fenetre. | ratio `[0, 1]` |
| `startup_latency_seconds` | Temps entre le debut de la fenetre et le premier segment de parole qui l'intersecte. Zero si la parole est deja active; duree de la fenetre si aucune parole n'est detectee. | secondes `[0, duree]` |

Les frontieres de mots suivent la convention semi-ouverte `[debut, fin)`: un mot exactement au debut est inclus, un mot exactement a la fin est exclu. Les valeurs restent numeriques dans tous les cas.

### Cache et sortie

Le cache global se trouve par defaut dans `.cache/candidate_analysis/` et n'est pas versionne. Sa cle SHA-256 depend de la version d'analyse, du chemin absolu, de la taille et du `mtime` de la source, ainsi que des reglages FFmpeg/transcription. Les durees de fenetre, le pas et la liste d'hesitations n'invalident pas la transcription: ils sont agreges a nouveau a faible cout.

Extrait de sortie:

```json
{
  "source_video": "video.mp4",
  "analysis_version": "1.0",
  "config": {
    "window_durations_seconds": [60.0, 75.0, 90.0],
    "step_seconds": 3.0,
    "silence_threshold_db": -35.0
  },
  "candidates": [
    {
      "candidate_id": "clip_0001",
      "start_seconds": 0.0,
      "end_seconds": 60.0,
      "duration_seconds": 60.0,
      "metrics": {
        "silence_ratio": 0.08,
        "longest_silence_seconds": 1.42,
        "speech_density": 0.72,
        "words_per_minute": 164.3,
        "hesitation_ratio": 0.031,
        "startup_latency_seconds": 0.38
      }
    }
  ]
}
```

### Limites connues

- La qualite de `speech_density`, des mots et des hesitations depend du modele, de la langue, du bruit et de la qualite audio.
- Les segments Faster-Whisper sont une estimation de la parole; ce n'est pas une annotation humaine au niveau phoneme.
- `silencedetect` mesure un niveau audio, pas la parole: musique et bruit peuvent donc expliquer qu'une zone ne soit pas silencieuse sans etre parlee.
- Le cache utilise chemin, taille et `mtime`, pas le hash integral du fichier, afin d'eviter une lecture supplementaire couteuse des longues videos.
- Le premier lancement d'un modele nomme necessite son telechargement; la vitesse et l'espace disque dependent du modele et du materiel.

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

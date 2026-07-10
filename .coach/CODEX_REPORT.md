# CODEX REPORT — M-001 — Cartographie du système existant

Date de l'audit : 2026-07-10
Type : `AUDIT_ONLY`
Statut : terminé
Périmètre autorisé et édité pendant cette tentative : ce rapport uniquement

## 1. Résumé

**FAIT VÉRIFIÉ** — Le dépôt contient deux sous-systèmes distincts :

1. un pipeline TikTok local qui détecte ou reçoit une vidéo YouTube, la télécharge,
   la découpe à intervalles fixes, produit des vidéos verticales et peut les envoyer
   à TikTok via l'API officielle ;
2. un orchestrateur Coach qui lance des rôles Codex séparés, exécute les tests du
   dépôt, fait relire les missions et conserve ses propres états et journaux.

**FAIT VÉRIFIÉ** — Le pipeline vidéo n'effectue actuellement aucune sélection de
passages fondée sur le contenu. Il n'extrait aucune mesure audio, visuelle ou
textuelle et ne calcule aucun `IntrinsicScore` ou `AccountFitScore`. Le découpage
est déterministe et temporel, à 60 secondes par défaut. Il n'existe pas non plus de
génération de sous-titres ou de transcription.

**FAIT VÉRIFIÉ** — La commande `git status --porcelain=v2`, exécutée avant toute
écriture de cette tentative, signalait déjà exactement deux chemins suivis
modifiés : `.coach/CODEX_REPORT.md` et `coach_system/state.json`. Cette tentative
a édité uniquement le rapport. Git ne permet pas d'établir l'auteur ni la
provenance de la modification de `coach_system/state.json`. Après les tests, son
empreinte SHA-256 vaut
`4BE76457854D5405FD087C4689B8B31BA04A5BCF2A1BF9FA08A770381451CE94`, identique à
l'empreinte déjà consignée dans le rapport présent au démarrage de cette
tentative ; cela établit l'absence de changement de contenu entre ces deux
observations, sans attribuer la modification initiale.

## 2. Arborescence utile et responsabilités

```text
.
├── src/
│   ├── youtube_recent_downloader.py  acquisition et pipeline vidéo principal
│   ├── control_app.py                serveur HTTP local et auto-runner
│   ├── tiktok_oauth.py               OAuth TikTok et stockage du token
│   └── tiktok_publisher.py           upload inbox / publication directe TikTok
├── tests/                             cinq modules unittest
├── web/                               dashboard HTML/CSS/JS servi localement
├── public/                            site public, pages légales et relais OAuth
├── data/                              TSV de chaînes, sources, synthèses, features
├── .state/                            états métier, tokens et logs locaux ignorés
├── downloads/                         sources, clips, shorts et exemples générés
├── videos_satisfaisantes/             vidéos de fond locales
├── assets/                            icône et autre dossier de médias satisfying
├── coach_system/                      superviseur Codex, prompts, schémas, état, logs
├── .coach/                            vision, mission, décisions, backlog et rapports
├── .env / .env.example               configuration locale / exemple versionné
├── requirements.txt                  dépendances Python déclarées
├── START_COACH.bat / STOP_COACH.bat  entrées Windows de l'orchestrateur
└── README.md / PROJECT_BRIEF.md       documentation générale
```

Responsabilités vérifiées :

- `src/` concentre tout le runtime métier. `youtube_recent_downloader.py` fait à
  lui seul acquisition, persistance, découpage, rendu, publication et nettoyage.
- `web/` est l'interface locale servie par `SimpleHTTPRequestHandler`. Son
  JavaScript appelle uniquement les routes HTTP locales `/api/*`.
- `public/` contient le site de présentation, les conditions, la politique de
  confidentialité, les informations d'app review et la page HTTPS qui retransmet
  le callback OAuth vers `127.0.0.1:8787`.
- `data/` contient deux jeux TSV de chaînes YouTube, leurs synthèses et sources,
  ainsi que `features.json`, registre affiché par le dashboard. `clip_finder` y est
  marqué `planned`, pas implémenté.
- `.state/` contient les états d'exécution et des données sensibles locales. Le
  dossier entier est ignoré par Git.
- `downloads/` contient les téléchargements YouTube (`.mp4`, `.info.json`,
  miniatures), les sous-dossiers `clips/` et `shorts/`, ainsi que des exemples.
  Lors de l'audit il représentait environ 3,13 Go sur disque.
- `coach_system/` est indépendant du pipeline TikTok : superviseur standard-library,
  configuration, état de reprise, prompts de rôles, schémas JSON et artefacts par
  cycle/tentative.
- `.coach/` porte la gouvernance du projet. Les quatre documents obligatoires ont
  été lus avant cet audit.

## 3. Points d'entrée

### 3.1 CLI métier

`python src/youtube_recent_downloader.py [options]`

- Entrées et sélection de source : `--channels-file`, `--video-url`,
  `--manual-channel`, `--manual-niche`, `--allowed-niches`.
- Fenêtre de scan : `--since-hours`, `--max-videos-per-channel`, `--limit`,
  `--include-undated`, `--force-resolve`, `--sleep-seconds`, `--dry-run`.
- Téléchargement et stockage : `--output-root`, `--state-file`, `--format`,
  `--cookies-from-browser`.
- Traitement : `--clip-segment-seconds`, `--satisfying-root`, `--skip-split`,
  `--skip-vertical-render`.
- TikTok : `--auto-publish-tiktok`, `--tiktok-access-token`,
  `--tiktok-publish-state-file`, `--tiktok-privacy-level`,
  `--tiktok-caption-template`, `--tiktok-publish-limit`,
  `--tiktok-publish-mode {auto,direct,upload}`,
  `--tiktok-publish-delay-min-seconds`,
  `--tiktok-publish-delay-max-seconds`, `--tiktok-chunk-size`,
  `--tiktok-disable-comment`, `--tiktok-disable-duet`,
  `--tiktok-disable-stitch`, `--tiktok-is-aigc`,
  `--tiktok-force-publish`, `--keep-files-after-publish` et
  `--keep-published-shorts`.

`python src/tiktok_publisher.py [options]`

- Lit les shorts rendus dans l'état du downloader puis publie ou charge ceux qui
  ne sont pas déjà connus.
- Paramètres : `--youtube-state-file`, `--publish-state-file`, `--access-token`,
  `--privacy-level`, `--caption`, `--caption-template`, `--limit`,
  `--publish-mode {auto,direct,upload}`, `--chunk-size`,
  `--disable-comment`, `--disable-duet`, `--disable-stitch`, `--is-aigc`,
  `--force` et `--dry-run`.

`python src/control_app.py`

- Aucun argument CLI. Charge `.env`, restaure l'état du dernier job et de la
  boucle, démarre un thread d'automation puis écoute sur `CONTROL_APP_HOST` et
  `CONTROL_APP_PORT` (`127.0.0.1:8787` par défaut).

### 3.2 HTTP local

Routes GET :

- `/` et les fichiers de `web/` : dashboard statique ;
- `/api/dashboard` : synthèse chaînes, vidéos, stockage, TikTok, job et boucle ;
- `/api/job` : job courant ;
- `/api/automation` : état de l'auto-runner ;
- `/api/tiktok/status` : statut OAuth public, sans token ;
- `/api/tiktok/connect` : redirection vers l'autorisation TikTok ;
- `/tiktok/callback?code=&state=` : vérification du state, échange du code et
  stockage local du token.

Routes POST :

- `/api/jobs` : lance le downloader. Payload principal : mode dry-run, URL
  manuelle, chaîne/niche, fenêtre, limite, cookies, stockage, niches, découpage,
  rendu et paramètres TikTok ;
- `/api/satisfying-jobs` : télécharge par `python -m yt_dlp` une URL YouTube dans
  `videos_satisfaisantes/` ;
- `/api/jobs/stop` : termine le subprocess courant ;
- `/api/automation` : active/désactive la boucle et persiste son payload ;
- `/api/tiktok/disconnect` : supprime le token local ;
- `/api/folders/open` : ouvre l'un des dossiers autorisés (`downloads`,
  `satisfying`, `project`, `state`) ;
- `/api/cleanup-failed` : supprime les artefacts d'une vidéo en échec si leurs
  chemins restent dans le workspace.

### 3.3 Windows et Coach

- `START_COACH.bat [arguments]` : configure l'encodage UTF-8, efface un ancien
  signal d'arrêt et appelle `python -m coach_system.supervisor %*`.
- `STOP_COACH.bat` : crée `coach_system/STOP_REQUESTED` pour un arrêt coopératif.
- `python -m coach_system.supervisor` accepte `--dry-run`, `--max-cycles`,
  `--config`, `--state` et `--repo`.
- Le superviseur invoque `codex login status`, puis `codex exec` avec sandbox
  `read-only` pour Coach/Reviewer/Scientist et `workspace-write` pour Engineer.
  Il peut exécuter les tests détectés et créer un commit local ; aucun push n'est
  codé.

### 3.4 Entrée OAuth publique

`public/tiktok/callback/index.html` reçoit la query string du callback HTTPS
`tiktok.aemour.com` et la retransmet, côté navigateur, à
`http://127.0.0.1:8787/tiktok/callback`. Les autres fichiers de `public/` sont des
pages statiques, pas un serveur applicatif.

## 4. Chemins de traitement vidéo

### 4.1 Acquisition commune

1. `load_dotenv` charge `.env` sans écraser les variables déjà présentes.
2. `load_state` charge le cache des chaînes, la liste des IDs téléchargés et les
   enregistrements vidéo.
3. En mode automatique, `load_creator_rows` lit le TSV, filtre les niches puis la
   limite. Une URL de chaîne est prise du TSV/cache ou résolue par une recherche
   `ytsearch5` via yt-dlp.
4. Avec un channel ID, le flux XML YouTube est préféré. En cas d'indisponibilité,
   yt-dlp lit l'onglet `/videos` et les métadonnées détaillées.
5. En mode manuel, yt-dlp lit directement `--video-url`.
6. Les candidats hors fenêtre et les IDs déjà connus sont écartés. Le dry-run
   persiste la détection mais ne télécharge aucune vidéo.
7. `download_video` demande à yt-dlp la vidéo jusqu'à 1080p et l'audio, fusionne
   vers MP4 et écrit aussi `.info.json` et une miniature sous
   `<output>/<niche>/<chaine>/`.

### 4.2 Chemin complet par lots

```text
vidéo téléchargée
  -> ffprobe : durée
  -> split_video_into_segments : parties fixes part-NNN.mp4
  -> render_vertical_shorts : short-NNN.mp4 pour chaque partie
  -> publication TikTok optionnelle
  -> nettoyage optionnel après publication
  -> état JSON mis à jour à chaque étape
```

- Le découpage par lots réencode chaque partie en H.264 (`libx264`, CRF 20,
  preset `veryfast`) et AAC 128 kbit/s.
- Le rendu final est 1080×1920 : moitié haute pour la vidéo principale ajustée
  sur un fond flouté, moitié basse pour une vidéo satisfying locale choisie au
  hasard et bouclée. L'audio vient du clip principal.
- Les clips sont écrits dans `clips/<slug>-<video_id>/`, les rendus dans
  `shorts/<slug>-<video_id>/`.
- Si aucune vidéo satisfying n'existe, le découpage reste disponible, le rendu
  retourne une liste vide et l'erreur est inscrite dans l'état.

### 4.3 Chemin streaming court par court

Ce chemin est choisi seulement si `auto_publish_tiktok` est vrai et si le rendu
vertical n'est pas désactivé.

1. La durée est mesurée et le nombre de parties est calculé.
2. Le nombre effectivement traité est plafonné par `tiktok_publish_limit`.
3. Pour chaque partie : `create_video_segment` extrait en copie de flux,
   `render_vertical_short_part` rend le vertical, puis
   `publish_single_short_to_tiktok` l'envoie.
4. L'état `pipeline_stage` progresse par `clipping`, `rendering`, `publishing`,
   `waiting`, puis `completed` ou `tiktok_failed`.
5. Un délai aléatoire borné sépare les envois. Si le nettoyage est actif, le clip
   et le short sont supprimés après l'envoi, puis la source et les dossiers vides
   sont nettoyés en fin de pipeline.

**FAIT VÉRIFIÉ** — Le scan automatique contient encore une seconde implémentation
en ligne du traitement, très proche de `process_download_candidate`, alors que le
mode URL manuelle utilise cette fonction. Les deux branches peuvent donc diverger.

### 4.4 Publication TikTok

- Le mode `auto` choisit Direct Post si le scope contient `video.publish`, sinon
  l'upload inbox si `video.upload` est présent.
- Direct Post interroge les informations créateur, valide le niveau de privacy,
  initialise la publication puis charge le fichier par morceaux.
- L'upload inbox initialise un dépôt inbox puis charge également par morceaux.
- Les résultats sont conservés dans l'état du downloader et dans un état de
  publication séparé pour éviter les doublons.
- `fetch_publish_status` existe mais n'est appelé par aucun flux actuel. Le système
  ne récupère donc pas automatiquement le statut final ni les performances TikTok.
- `REVOKE_URL` est déclaré, mais la déconnexion actuelle efface uniquement le
  fichier local et ne révoque pas le token côté TikTok.

## 5. Dépendances, exécutables, réseau et IA

### 5.1 Dépendances Python et exécutables

**FAIT VÉRIFIÉ** — `requirements.txt` déclare uniquement
`yt-dlp>=2026.7.4`. Le reste du runtime Python utilise la bibliothèque standard.

Exécutables requis ou détectés par le code :

- Python pour les quatre entrées Python et le superviseur ;
- `ffmpeg` et `ffprobe` pour le probe, le découpage et le rendu ;
- `git` et `codex` pour l'orchestrateur Coach ;
- `cmd.exe`/batch et `chcp` pour les entrées Windows ;
- `npm` seulement si un futur `package.json` contient un script `test` ; aucun
  `package.json` n'existe actuellement ;
- Explorateur Windows via `os.startfile` pour l'ouverture locale des dossiers.

Environnement observé pendant l'audit :

- Windows 11, build rapporté par Python `10.0.26200`, architecture AMD64 ;
- Python 3.13.5 ;
- yt-dlp 2026.07.04 ;
- FFmpeg et ffprobe 7.1.1 (`essentials_build-www.gyan.dev`) ;
- Git 2.55.0.windows.2 ;
- `codex`, Node et npm présents sur le PATH, sans exécution réelle de Codex pour
  cette mission.

### 5.2 Appels réseau et API externes

- YouTube : flux `https://www.youtube.com/feeds/videos.xml`, pages de chaînes,
  recherches, métadonnées et téléchargements par yt-dlp.
- TikTok OAuth : autorisation `www.tiktok.com/v2/auth/authorize/` et token/refresh
  `open.tiktokapis.com/v2/oauth/token/`.
- TikTok Content Posting : creator info, initialisation Direct Post, initialisation
  inbox, endpoint de statut et URL d'upload retournée par TikTok.
- Callback public : `https://tiktok.aemour.com/tiktok/callback/`, puis relais HTTP
  vers le serveur local.
- Coach : `codex login status` et `codex exec` peuvent utiliser la session Codex
  installée ; ce sous-système n'est pas appelé par le pipeline vidéo.

### 5.3 Présence ou absence d'IA

**FAIT VÉRIFIÉ** — Aucun poids de modèle (`.onnx`, `.pt`, `.pth`, `.safetensors`,
`.gguf`, etc.), framework ML, Whisper, embedding ou modèle local n'est présent.
Aucun appel OpenAI, Anthropic ou Gemini n'existe dans `src/`. Le pipeline vidéo ne
contient donc aucun modèle IA.

**LIMITATION** — `coach_system/` orchestre la CLI Codex avec une authentification
locale existante. Il s'agit d'un outil de développement séparé et non d'un modèle
embarqué ou d'une dépendance d'analyse vidéo.

## 6. Configuration, états et journaux

### 6.1 Fichiers de configuration

- `.env` : configuration locale ignorée ; son existence a été constatée sans
  reproduire aucune valeur.
- `.env.example` : liste versionnée des réglages documentés.
- `requirements.txt` : dépendance Python.
- `data/top_100_youtubeurs_fr_by_niche.tsv` : source par défaut des chaînes.
- `data/features.json` : registre de fonctionnalités affiché par le dashboard.
- `coach_system/config.json` : cycles, retries, timeouts, tests, commit et propreté
  Git.
- `coach_system/schemas/*.json` et `prompts/*.md` : contrats et rôles des agents.
- `.gitignore` : exclut secrets, états, médias générés, caches, logs et signaux.

### 6.2 Variables d'environnement référencées

Valeurs non lues et non exposées dans ce rapport :

- Contrôle : `CONTROL_APP_HOST`, `CONTROL_APP_PORT`.
- YouTube/stockage : `VIDEO_OUTPUT_ROOT`, `YOUTUBE_DOWNLOADER_STATE`,
  `YOUTUBE_ALLOWED_NICHES`, `YOUTUBE_SINCE_HOURS`,
  `YOUTUBE_MAX_VIDEOS_PER_CHANNEL`, `YOUTUBE_DOWNLOAD_FORMAT`,
  `YOUTUBE_COOKIES_FROM_BROWSER`, `YOUTUBE_SLEEP_SECONDS`.
- Traitement : `CLIP_SEGMENT_SECONDS`, `SATISFYING_VIDEO_ROOT`.
- OAuth TikTok : `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`,
  `TIKTOK_REDIRECT_URI`, `TIKTOK_LOCAL_CALLBACK_URL`, `TIKTOK_SCOPES`,
  `TIKTOK_ACCESS_TOKEN`.
- Publication TikTok : `TIKTOK_AUTO_PUBLISH`, `TIKTOK_PRIVACY_LEVEL`,
  `TIKTOK_CAPTION_TEMPLATE`, `TIKTOK_PUBLISH_LIMIT`, `TIKTOK_PUBLISH_MODE`,
  `TIKTOK_PUBLISH_STATE`, `TIKTOK_PUBLISH_DELAY_MIN_SECONDS`,
  `TIKTOK_PUBLISH_DELAY_MAX_SECONDS`, `TIKTOK_CHUNK_SIZE`,
  `TIKTOK_DISABLE_COMMENT`, `TIKTOK_DISABLE_DUET`,
  `TIKTOK_DISABLE_STITCH`, `TIKTOK_IS_AIGC`,
  `TIKTOK_CLEANUP_AFTER_PUBLISH`, `TIKTOK_KEEP_PUBLISHED_SHORTS`.
- Batch Windows : `PYTHONUTF8`, `PYTHONIOENCODING` sont définies au lancement.

**PROBLÈME NON RÉSOLU** — Onze variables utilisées par le code ne figurent pas
dans `.env.example` : `CONTROL_APP_HOST`, `CONTROL_APP_PORT`,
`YOUTUBE_DOWNLOADER_STATE`, `YOUTUBE_ALLOWED_NICHES`, `TIKTOK_PUBLISH_STATE`,
`TIKTOK_PUBLISH_MODE`, `TIKTOK_PUBLISH_DELAY_MIN_SECONDS`,
`TIKTOK_PUBLISH_DELAY_MAX_SECONDS`, `TIKTOK_CHUNK_SIZE`,
`TIKTOK_CLEANUP_AFTER_PUBLISH`, `TIKTOK_KEEP_PUBLISHED_SHORTS`.

### 6.3 États persistants et journaux

- `.state/youtube_recent_downloader.json` : chaînes résolues, IDs connus, vidéos,
  chemins, statuts de clip/rendu/publication/nettoyage et progression du pipeline.
- `.state/tiktok_token.json` : token OAuth et échéances ; sensible, ignoré.
- `.state/tiktok_oauth_state.json` : nonce CSRF et date de création.
- `.state/tiktok_publish_state.json` : publication anti-doublon par chemin ; chemin
  par défaut prévu par le code, fichier absent lors de l'audit.
- `.state/tiktok_last_test_upload.json` : trace locale d'un upload de test existant.
- `.state/control_app_job.json` : commande, statut, code retour et jusqu'à 500
  lignes de log du job.
- `.state/control_app_loop.json` : activation, intervalle, prochain passage et
  payload de l'auto-runner.
- `.state/control_app_server.log`, `.out.log`, `.err.log` : fichiers présents ; le
  serveur applicatif lui-même neutralise `log_message`, ces redirections ne sont
  pas configurées dans `control_app.py`.
- `coach_system/state.json` : mission active, missions terminées/bloquées, reprise.
- `coach_system/logs/<run>/cycle_NNN/attempt_NN/` : sorties agents, événements,
  tests, diff, reviews et résumés. Ces logs sont ignorés sauf `.gitkeep`.

## 7. Composants critiques et réutilisables

- Acquisition : `load_creator_rows`, `resolve_channel_url`,
  `fetch_feed_candidates`, `fetch_recent_candidates`,
  `fetch_video_candidate_from_url`, `download_video`.
- Média : `probe_duration_seconds`, `split_video_into_segments`,
  `create_video_segment`, `render_vertical_short`, `render_vertical_shorts`.
- Persistance explicable : `record_video_state`, `record_clip_state`,
  `record_render_state`, `record_pipeline_progress`,
  `record_tiktok_publish_state`.
- TikTok : `get_valid_access_token`, `choose_publish_mode`,
  `publish_or_upload_short`, `upload_video_chunks`.
- Contrôle : `build_download_command`, `run_job`, `automation_loop`,
  `summarize_dashboard`.
- Coach : détection de tests, exécution subprocess UTF-8, validation JSON,
  verrou d'instance, arrêt/reprise et journalisation par tentative.

Ces éléments ont des responsabilités identifiables et peuvent constituer des
frontières d'intégration sans réécrire le téléchargement, le rendu ou la
publication.

## 8. Tests : inventaire et baseline

### 8.1 Inventaire des 50 méthodes détectées

`tests/test_coach_supervisor.py` — 21 tests unitaires/mockés :

- `test_codex_utf8_bytes_not_decodable_as_cp1252`,
  `test_codex_french_accents_are_preserved`, `test_codex_em_dash_is_preserved`,
  `test_codex_unicode_arrow_is_preserved`, `test_codex_emoji_is_preserved`,
  `test_codex_stdout_and_stderr_none_are_empty`,
  `test_codex_valid_stdout_and_stderr_none_continue`,
  `test_codex_stdout_none_and_valid_stderr_continue`,
  `test_run_checked_uses_utf8_replace_and_normalizes_none`, `test_codex_absent`,
  `test_repository_not_git`, `test_invalid_json`, `test_agent_timeout`,
  `test_failing_test_command_is_captured`,
  `test_reviewer_reject_returns_to_engineer`,
  `test_scientist_insufficient_evidence_returns_to_engineer`,
  `test_retry_limit_blocks_mission`, `test_max_cycles_is_respected_in_dry_run`,
  `test_stop_file_prevents_agent_work`,
  `test_interrupted_active_mission_is_resumed`,
  `test_lock_prevents_double_instance`.

`tests/test_control_app.py` — 9 tests unitaires/mockés :

- `test_safe_int_clamps_values`, `test_build_download_command_keeps_dry_run_and_limit`,
  `test_default_loop_payload_keeps_tiktok_publish_disabled`,
  `test_build_download_command_adds_allowed_niches`,
  `test_build_download_command_adds_tiktok_publish_only_when_enabled`,
  `test_build_download_command_accepts_manual_video_url`,
  `test_build_satisfying_command_downloads_into_satisfying_folder`,
  `test_folder_target_allows_known_folders_only`,
  `test_dashboard_exposes_tiktok_stats_and_video_status`.

`tests/test_tiktok_oauth.py` — 4 tests unitaires/mockés :

- `test_build_authorization_url_uses_v2_endpoint_and_scopes`,
  `test_state_round_trip`, `test_exchange_code_persists_enriched_token`,
  `test_public_status_reports_missing_config`.

`tests/test_tiktok_publisher.py` — 7 tests unitaires :

- `test_make_chunk_plan_for_small_video`, `test_make_chunk_plan_for_large_video`,
  `test_make_chunk_plan_uses_tiktok_floor_count_for_partial_final_chunk`,
  `test_mime_type_for_video`,
  `test_validate_creator_can_publish_rejects_bad_privacy`,
  `test_choose_publish_mode_prefers_direct_when_publish_scope_exists`,
  `test_choose_publish_mode_falls_back_to_upload_scope`.

`tests/test_youtube_recent_downloader.py` — 9 tests :

- huit tests sans exécutable externe : `test_slugify_removes_accents_and_symbols`,
  `test_parse_upload_datetime_from_yt_dlp_date`,
  `test_parse_upload_datetime_from_timestamp`, `test_parse_channel_id_from_url`,
  `test_segment_count_for_duration_rounds_up`,
  `test_load_creator_rows_filters_allowed_niches_before_limit`,
  `test_cleanup_generated_after_publish_removes_generated_files`,
  `test_render_vertical_shorts_without_satisfying_video_does_not_crash` ;
- un test d'intégration conditionnel : `test_split_video_into_segments`, décoré
  par `skipUnless(ffmpeg && ffprobe)`. Il génère une fixture vidéo avec FFmpeg puis
  exerce réellement ffprobe et le découpage FFmpeg.

Total : 49 tests unitaires, mockés ou filesystem local sans service externe, et
1 test d'intégration FFmpeg conditionnel. Aucun test n'effectue d'appel YouTube,
TikTok ou Codex réel.

### 8.2 Baseline reproductible observée

- Commande : `python -m unittest discover -s tests -v`
- Répertoire : racine du dépôt
- Code de sortie : `0`
- Résultat unittest exact observé pendant cette tentative :
  `Ran 50 tests in 0.474s` puis `OK`
- Décompte observé : 50 réussis, 0 échoué, 0 ignoré
- Durée murale mesurée autour de cette commande seule avec
  `System.Diagnostics.Stopwatch` : sortie exacte
  `BASELINE_WALL_SECONDS=0,804` (locale française), soit `0.804 s`
- Test FFmpeg conditionnel : exécuté et réussi, non ignoré
- Environnement : Windows 11 AMD64, Python 3.13.5, yt-dlp 2026.07.04,
  FFmpeg/ffprobe 7.1.1

**FAIT VÉRIFIÉ** — Un artefact antérieur distinct,
`coach_system/logs/2026-07-10_182201_352048/cycle_001/attempt_01/tests.json`,
consigne `Ran 50 tests in 1.887s`, `OK` et `duration_seconds: 2.356`. Ces valeurs
appartiennent à l'exécution du superviseur précédent ; elles ne sont pas présentées
comme les durées de la nouvelle baseline ci-dessus.

**LIMITATION** — La RAM, la VRAM et le CPU précis n'ont pas été mesurés : l'accès
CIM au matériel a été refusé par l'environnement. Aucun benchmark de vidéo longue,
de rendu vertical, de réseau ou de publication n'a été lancé pendant cet audit.

## 9. Risques de régression et dettes techniques

### P0

- `youtube_recent_downloader.py` fait 1 835 lignes et mélange acquisition, état,
  média, TikTok et nettoyage. Toute insertion dans ce fichier a un rayon de
  régression élevé.
- Le traitement est dupliqué entre `process_download_candidate` et la boucle de
  scan de `run`; un correctif appliqué à un chemin peut manquer l'autre.
- Les fichiers JSON métier sont réécrits directement, sans remplacement atomique
  ni verrou interprocessus commun. Le serveur, l'auto-runner et une CLI parallèle
  peuvent entrer en concurrence ou laisser un fichier partiel après interruption.
- Les routes HTTP locales n'ont ni authentification ni protection CSRF. Le host
  est local par défaut, mais le rendre accessible sur le réseau exposerait lancement,
  arrêt, ouverture de dossiers, déconnexion et suppression de fichiers.
- `control_app.py` injecte sans échappement HTML `error`, `error_description`, une
  exception d'échange OAuth et les scopes dans les réponses de `/api/tiktok/connect`
  et `/tiktok/callback`. Un paramètre ou une réponse OAuth contrôlée peut donc
  produire une XSS réfléchie sur l'origine locale. L'impact est aggravé par les
  routes POST sensibles de la même origine, sans authentification ni protection
  CSRF.
- La publication et le nettoyage sont des opérations à fort impact. Le nettoyage
  automatique ne valide pas explicitement toutes ses cibles contre `output_root` ;
  il dépend de chemins produits précédemment par le pipeline.

### P1

- Les chemins batch et streaming n'emploient pas la même stratégie de découpage :
  réencodage H.264/AAC pour le batch, copie de flux pour le streaming. Les bornes
  temporelles et la compatibilité des fichiers peuvent différer.
- Des clips existants sont réutilisés sans vérifier que `segment_seconds`, la source
  ou les paramètres d'encodage sont identiques.
- La sélection aléatoire de la vidéo satisfying n'est ni reproductible ni consignée
  avant le rendu ; l'état conserve la source choisie après coup.
- `fetch_publish_status` et la récupération des métriques réelles ne sont pas
  intégrés. Le dashboard additionne des clés de vues si elles existent, mais aucun
  flux ne les renseigne actuellement.
- La page callback publique fixe `127.0.0.1:8787`, même si les variables de host,
  port ou callback local changent.
- `.env.example` est incomplet par rapport aux 33 variables métier reconnues.
- Le mode `auto` de publication revient sur `direct` même si aucun scope compatible
  n'est présent ; l'échec n'est découvert qu'à l'appel API.

### P2

- `README.md` décrit encore plusieurs éléments comme initiaux ou à confirmer alors
  que le pipeline, le dashboard et TikTok existent déjà.
- `REVOKE_URL` et `fetch_publish_status` sont du code non raccordé.
- Les tests couvrent bien les fonctions déterministes, mais pas les routes HTTP de
  bout en bout, le rendu vertical FFmpeg avec une vraie source satisfying, la
  concurrence des états, les deux pipelines complets ni les réponses externes.
- Les exceptions externes peuvent être copiées telles quelles dans les états et
  logs métier ; aucun mécanisme général de redaction n'y est appliqué, contrairement
  aux journaux du superviseur Coach.

## 10. Recommandations priorisées

Ces recommandations délimitent les corrections et travaux préparatoires ; elles
ne constituent pas la conception du moteur de sélection.

### P0

- Échapper systématiquement toutes les valeurs dynamiques rendues en HTML dans
  `control_app.py`, ajouter des tests de non-régression XSS, puis protéger les
  routes mutantes par une authentification locale et une défense CSRF. Maintenir
  le bind sur loopback par défaut et refuser une exposition réseau accidentelle.
- Unifier l'orchestration du traitement automatique et manuel avant toute insertion
  du futur moteur, afin qu'une seule frontière appelle découpage, rendu,
  publication et nettoyage.
- Rendre les écritures JSON atomiques et définir un verrouillage interprocessus
  pour éviter corruption et concurrence entre CLI, serveur et auto-runner.
- Encadrer le nettoyage par des contrôles de chemins résolus sous les racines
  autorisées et conserver la publication TikTok désactivée par défaut.

### P1

- Définir une politique de découpage commune aux chemins batch et streaming et
  invalider les clips en cache lorsque la source ou les paramètres changent.
- Rendre le choix de vidéo satisfying reproductible ou au minimum persister le
  choix et les paramètres avant rendu.
- Compléter `.env.example`, valider les scopes avant publication et raccorder le
  suivi de statut TikTok sans exposer les tokens dans les logs.
- Ajouter des tests d'intégration ciblés pour les routes HTTP, la persistance
  concurrente, le vrai rendu vertical FFmpeg et les deux orchestrations complètes,
  avec tous les services réseau simulés.

### P2

- Aligner `README.md` sur le comportement réellement observé et documenter les
  états, logs et exécutables locaux requis.
- Décider explicitement du raccordement ou du retrait de `REVOKE_URL` et
  `fetch_publish_status`.
- Ajouter un mécanisme général de redaction des erreurs externes avant persistance
  dans les états ou journaux métier.

## 11. Frontières d'intégration possibles

**PROPOSITION** — Sans concevoir le futur moteur pendant M-001, la frontière la
moins intrusive se situe après `download_video` et `probe_duration_seconds`, avant
`split_video_into_segments` et `create_video_segment`. Le futur composant pourrait
recevoir un chemin source et retourner des fenêtres temporelles explicables, que
les fonctions existantes de découpage, rendu et publication consommeraient.

**PROPOSITION** — Pour éviter deux intégrations divergentes, le mode scan et le
mode URL manuelle devraient traverser une seule orchestration de candidat avant
d'ajouter cette frontière.

**PROPOSITION** — Les mesures brutes devraient rester dans un stockage distinct de
l'état opérationnel du downloader. Le dashboard peut ensuite les exposer sans
mélanger extraction, normalisation et score. Ces propositions respectent la chaîne
imposée `EXTRACTION -> MESURES BRUTES -> NORMALISATION -> SCORING` mais ne fixent
ni métriques, ni formule, ni modèle.

Composants réutilisables à conserver autour de cette frontière : acquisition et
cache YouTube, ffprobe/FFmpeg, structure de dossiers, rendu vertical, OAuth,
publication par morceaux, suivi de progression et interface de contrôle.

## 12. Hypothèses, limitations et problèmes non résolus

**HYPOTHÈSE** — Les fichiers présents sous `downloads/` et `.state/` sont des
artefacts d'exécutions antérieures. Leur présence prouve que certains chemins ont
produit des sorties, mais pas que les services externes fonctionnent encore au
2026-07-10.

**LIMITATIONS** — L'audit est statique, complété par la suite locale. Aucun scan
YouTube, téléchargement réseau, OAuth, upload, Direct Post, nettoyage réel,
auto-runner, agent Codex réel ou commit n'a été lancé. La validité actuelle des
permissions TikTok et des réponses YouTube n'a donc pas été vérifiée.

**PROBLÈMES NON RÉSOLUS** — Absence de sélection objective, de sous-titres, de
métriques TikTok récupérées, de verrouillage métier, de benchmark média et de test
de bout en bout. La cohérence des états locaux existants avec les fichiers présents
n'a pas été validée, afin de rester dans le périmètre d'audit sans mutation.

Question bloquante pour M-001 : aucune.

## 13. Fichiers modifiés et résultat final

- Édité pendant cette tentative : `.coach/CODEX_REPORT.md` uniquement.
- Code, tests, configuration, schémas et médias : aucune édition effectuée.
- Worktree initial : `.coach/CODEX_REPORT.md` et `coach_system/state.json` étaient
  déjà signalés modifiés. Le contrôle final doit comparer cet état initial à
  l'état final ; il n'est donc pas factuellement possible d'affirmer que le
  worktree global ne contient que le rapport.
- `coach_system/state.json` : non édité pendant cette tentative. Son diff courant
  contient l'objet `active_mission` M-001 à la place de `null`. Sa provenance
  n'est pas attribuée. L'empreinte post-tests est indiquée dans le résumé et
  correspond à celle déjà inscrite dans le rapport au début de cette tentative.
- Tests lancés : baseline unittest complète, 50/50 réussis.
- Benchmark média : non exécuté, non requis pour cet audit et potentiellement
  coûteux.

---

## 14. Correctif systémique de traçabilité du superviseur — 2026-07-10

### Résumé

**FAIT VÉRIFIÉ** — Le cycle réel `2026-07-10_182201_352048` exécutait bien la
séquence `Engineer -> tests superviseur -> Reviewer`. L'Engineer pouvait consigner
les timings de son propre run A, puis le superviseur créait un run B distinct dans
`tests.json`. Le Reviewer comparait les deux comme s'ils décrivaient la même
exécution. Les trois runs superviseur observés ont produit respectivement
`Ran 50 tests in 1.887s`, `0.764s` et `0.483s`, ce qui confirme que ces timings
varient naturellement entre exécutions.

**FAIT VÉRIFIÉ** — Avant correction, `git_diff.patch` était calculé contre `HEAD`
et excluait déjà `coach_system/state.json` par pathspec ; les trois anciens patches
ne contiennent que `.coach/CODEX_REPORT.md`. Toutefois le Reviewer read-only pouvait
inspecter directement le worktree global et y voyait `state.json`, modifié lorsque
le superviseur avait écrit `active_mission`. Aucun artefact de baseline ni contexte
structuré ne permettait alors d'en prouver la provenance runtime. L'exclusion seule
du patch était donc insuffisante et ambiguë.

### Architecture mise en place

1. `tests.json` porte maintenant la source
   `SUPERVISOR_AUTHORITATIVE_TEST_RUN` et constitue l'unique résultat autoritaire
   pour l'acceptation.
2. Les éventuels tests Engineer sont étiquetés `ENGINEER_SELF_TEST_RUN`. Leurs
   timings ne sont jamais exigés identiques à ceux d'un autre run.
3. Une baseline est capturée au début de la mission et avant chaque tentative :
   commit Git, status, horodatage et SHA-256 de chaque fichier métier.
4. Le Reviewer reçoit séparément le diff métier cumulatif depuis le début de la
   mission et le delta de la tentative courante.
5. La liste runtime exclue est minimale et explicite :
   `coach_system/state.json`, `coach_system/logs/**`, le lock et le signal d'arrêt.
   Aucune modification de code ou de rapport n'est masquée.
6. Les verdicts Reviewer classent les issues en `ENGINEER_FIXABLE`,
   `SUPERVISOR_INFRASTRUCTURE`, `EXTERNAL_BLOCKER` ou `NONE`. Les deux catégories
   non corrigeables par l'Engineer arrêtent immédiatement les retries.

### Fichiers modifiés pendant ce correctif

- `coach_system/supervisor.py` ;
- `coach_system/prompts/engineer.md` ;
- `coach_system/prompts/reviewer.md` ;
- `coach_system/schemas/review.schema.json` ;
- `coach_system/README.md` ;
- `tests/test_coach_supervisor.py` ;
- `.coach/CODEX_REPORT.md`.

**FAIT VÉRIFIÉ** — `coach_system/state.json` était déjà modifié par le cycle réel
avant cette mission et n'a pas été édité par ce correctif.

### Tests ajoutés

Huit tests mockés couvrent :

- run Engineer à `0.631 s` et run superviseur distinct à `0.764 s` sans faux rejet ;
- transmission de `tests.json` comme source autoritaire au Reviewer ;
- exclusion d'une mutation runtime de `state.json` du diff métier ;
- conservation visible d'une vraie modification hors périmètre ;
- distinction entre modification préexistante métier et runtime ;
- baseline Git avec commit, status et fichiers métier, sans contenu runtime ;
- arrêt après une issue `SUPERVISOR_INFRASTRUCTURE` sans épuiser trois retries ;
- maintien du blocage lorsque les tests autoritaires échouent réellement.

### Résultats exacts

- tests ciblés orchestrateur : 29 réussis en `0.111 s` ;
- suite complète : 58 réussis en `0.728 s`, exit code `0` ;
- `python -m compileall coach_system` : exit code `0` ;
- `git diff --check` : exit code `0` ;
- `START_COACH.bat --dry-run --max-cycles 1` : exit code `0` ;
- dry-run : diff métier cumulatif `0` octet, delta tentative `0` octet ;
- aucun `codex exec` réel, push Git ou appel TikTok lancé.

### Hypothèses et limites

**HYPOTHÈSE** — Une classification globale suffit : si un rejet mélange une issue
Engineer et une issue infrastructure, le Reviewer doit utiliser
`ENGINEER_FIXABLE`, comme indiqué dans son prompt.

**LIMITATION** — Les snapshots couvrent les fichiers suivis et non suivis non
ignorés retournés par Git. Les fichiers ignorés ne sont pas inclus dans le diff
métier ; seuls les chemins runtime ignorés pertinents sont néanmoins documentés
dans la politique d'exclusion. Les fichiers binaires modifiés sont signalés comme
différents sans exposer leur contenu. Le nouveau flux n'a volontairement été
validé qu'avec mocks et dry-run, pas par un cycle Codex réel.

---

## 15. MVP d'analyse objective des passages candidats — 2026-07-10

### Résumé et architecture existante pertinente

**FAIT VÉRIFIÉ** — Le pipeline existant est centré sur
`src/youtube_recent_downloader.py`. `yt-dlp` acquiert les vidéos, FFprobe mesure
leur durée, FFmpeg crée les clips et rend les shorts verticaux, puis l'API
officielle TikTok peut publier les fichiers si l'option explicite est activée.
Les modes scan et URL manuelle restent distincts. Aucun code de téléchargement,
rendu, publication, OAuth ou `coach_system` n'a été modifié pendant cette mission.

**DÉCISION TECHNIQUE** — Le moteur est un paquet indépendant
`src.candidate_analysis`, placé à la frontière naturelle entre vidéo locale et
découpage. Il n'est pas encore injecté dans l'orchestration existante, afin de ne
pas risquer la publication. Il exécute FFprobe, FFmpeg `silencedetect` et
Faster-Whisper une seule fois par source/configuration, met en cache les timelines,
génère des fenêtres virtuelles, puis agrège exactement six mesures brutes. Aucun
fichier candidat n'est créé et aucun score n'est calculé.

### Fichiers créés

- `requirements-analysis.txt` ;
- `src/candidate_analysis/__init__.py` ;
- `src/candidate_analysis/__main__.py` ;
- `src/candidate_analysis/analyzer.py` ;
- `src/candidate_analysis/audio_analysis.py` ;
- `src/candidate_analysis/cache.py` ;
- `src/candidate_analysis/cli.py` ;
- `src/candidate_analysis/metrics.py` ;
- `src/candidate_analysis/schemas.py` ;
- `src/candidate_analysis/transcription.py` ;
- `src/candidate_analysis/windows.py` ;
- `tests/test_candidate_analysis.py`.

### Fichiers modifiés pendant cette mission

- `.gitignore` : exclusion du cache local `.cache/` ;
- `README.md` : installation, CLI, architecture, six définitions, cache et limites ;
- `.coach/CODEX_REPORT.md` : présent rapport.

Les modifications `coach_system/**` et `tests/test_coach_supervisor.py` visibles
dans le worktree sont antérieures à cette mission. `coach_system/state.json` est
également une mutation runtime préexistante. Elles ont été préservées et non
retouchées.

### Dépendances et traitement local

- Nouveau groupe optionnel : `faster-whisper>=1.1,<2`, qui entraîne ses
  dépendances locales CTranslate2, PyAV, ONNX Runtime et Hugging Face Hub.
- Versions réellement validées : Faster-Whisper `1.2.1`, CTranslate2 `4.8.1`,
  FFmpeg `7.1.1`, Python `3.13.5`.
- Le modèle `tiny` a été téléchargé dans le cache Hugging Face lors du premier
  test, puis exécuté localement. Aucune API IA payante, clé ou souscription.
- Le paquet `idna` installé dans l'environnement était incomplet et a causé deux
  échecs initiaux avant analyse. Une réinstallation locale en version `3.18` a
  réparé l'environnement ; le code du projet n'a pas masqué cette exception.

### Définition des six métriques

1. `silence_ratio = durée union des silences FFmpeg intersectés / durée candidat`,
   ratio `[0,1]` ; seuil dB et durée minimale enregistrés dans la configuration.
2. `longest_silence_seconds = max(durée de chaque silence continu intersecté)`,
   secondes `[0,durée candidat]`.
3. `speech_density = durée union des segments Faster-Whisper/VAD intersectés /
   durée candidat`, ratio `[0,1]`. Cette timeline est indépendante de
   `silence_ratio` et les sons non transcrits ne deviennent pas arbitrairement de
   la parole.
4. `words_per_minute = nombre de tokens / secondes de parole actives * 60`, en
   mots/minute ; `0` si la parole active est nulle.
5. `hesitation_ratio = occurrences des mots/expressions configurés / nombre total
   de tokens`, ratio `[0,1]` ; liste française explicite par défaut.
6. `startup_latency_seconds = premier début de parole intersecté - début candidat`,
   secondes `[0,durée candidat]`; `0` si la parole était déjà active et durée du
   candidat si aucune parole n'est détectée.

Les mots utilisent la frontière semi-ouverte `[début, fin)`. Tous les cas sans
parole ou entièrement silencieux conservent des nombres JSON.

### Tests ajoutés et résultats

**RÉSULTAT DE TEST** — 23 tests unitaires de non-régression ont été ajoutés :
fenêtres et frontières, vidéo trop courte, paramètres invalides, six calculs,
indépendance silence/parole, division par zéro, hésitations simples et composées,
parole déjà active, aucune parole, silence total, parsing `silencedetect`, cache,
invalidation de source, analyse globale unique, JSON et CLI mockée.

- baseline avant édition : `58` réussis, `0` échec, durée unittest `0.625 s` ;
- suite ciblée : `22` réussis, `0` échec, durée unittest `0.044 s` ;
- première suite complète après implémentation : `80` réussis, `0` échec,
  durée unittest `0.586 s`.

Après relecture défensive, un test supplémentaire vérifie que des expressions
d'hésitation dupliquées ou chevauchantes restent non chevauchantes et que le ratio
reste borné. Les résultats finaux sont consignés ci-dessous.

- suite complète finale : `81` réussis, `0` échec, durée unittest `0.535 s` ;
- `python -m compileall src` : exit code `0` ;
- `git diff --check` : exit code `0` ;
- aide CLI `python -m src.candidate_analysis --help` : exit code `0`.

### Validation réelle et benchmark

**RÉSULTAT DE TEST** — Deux vidéos existantes ont été lues sans modification et
aucune publication n'a été lancée :

- `downloads/examples/example-main-horizontal.mp4`, durée `5.0 s`, CPU,
  `tiny/int8`, fenêtres `3 s`, pas `1 s` : `3` candidats ; analyse globale froide
  `5.790 s`, relecture du cache `0.105 s`. Aucune parole détectée, ce qui valide le
  chemin numérique sans parole.
- premier clip Gotaga local, `64,619,782` octets, durée `60.007 s`, CPU,
  `tiny/int8`, fenêtre `60 s` : `1` candidat ; première mesure `23.779 s`, puis
  benchmark répété sans cache d'analyse `25.446 s`, pic de working set observé
  `322.7 MiB`.

Matériel : Intel Core i5-11400H, NVIDIA GTX 1650 et Intel UHD Graphics. Le
benchmark a explicitement utilisé le CPU ; VRAM non utilisée/non mesurée. Les
métriques du candidat Gotaga étaient : silence `0.0`, plus long silence `0.0 s`,
densité de parole `0.7181666667`, WPM `98.8628452077`, hésitation `0.0`, latence
initiale `3.54 s`. Ces valeurs sont des sorties du modèle `tiny`, pas une vérité
terrain ni un score.

### Commande et limites

Commande utilisateur :

```powershell
python -m src.candidate_analysis analyze "video.mp4" --step 3 --durations 60,75,90,105,120 --output analysis.json
```

**LIMITATIONS** — Le modèle et sa qualité influencent parole, mots, WPM et
hésitations. `silencedetect` détecte un niveau sonore et non la parole. Le modèle
doit être téléchargé au premier usage si aucun chemin local n'est fourni. Le cache
source utilise chemin, taille et `mtime`, pas un hash complet, pour éviter une
lecture supplémentaire des longues vidéos. Le moteur n'est volontairement pas
raccordé automatiquement au downloader, au rendu ou à TikTok. La prochaine étape
recommandée est d'établir une petite fixture vidéo parlée annotée manuellement afin
de mesurer l'erreur des timelines et de comparer `tiny`/`small` avant toute logique
de sélection ou de scoring.

---

## 16. Mise à jour du site public et publication GitHub — 2026-07-10

### Site public

**FAIT VÉRIFIÉ** — `public/CNAME` désigne `tiktok.aemour.com`. La page publique
présente maintenant le moteur local sans surpromesse : analyse globale unique,
fenêtres virtuelles, six métriques brutes, cache, absence de score viral subjectif
et publication toujours soumise au consentement explicite.

Fichiers modifiés :

- `public/index.html` : métadonnées, nouveau positionnement local-first, cartes
  produit, section détaillant les six métriques et workflow responsive ;
- `public/app-review.html` : description TikTok complétée pour distinguer analyse
  locale et publication officielle.

**RÉSULTAT DE TEST** — Les quatre pages HTML publiques sont structurellement
valides selon `html.parser`. Le serveur local a répondu HTTP `200` pour `/`,
`/terms.html`, `/privacy.html` et `/app-review.html`. Aucun navigateur pilotable
n'était disponible dans la session ; une inspection visuelle automatisée n'a donc
pas pu être produite. La suite complète finale a réussi `81` tests, `0` échec,
en `1.986 s`; `python -m compileall src` et `git diff --check` ont réussi.

### Publication GitHub

**PROBLÈME NON RÉSOLU** — La publication est bloquée avant commit : le dépôt local
n'a aucun remote Git configuré (`git remote -v` vide) et GitHub CLI `gh` n'est pas
installé. Conformément au workflow de publication, aucun remote n'a été inventé,
aucun commit partiel n'a été créé et aucun push n'a été tenté. Le fichier runtime
`coach_system/state.json` ne devra pas être inclus dans le futur commit.

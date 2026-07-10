# CODEX REPORT — M-001 / Orchestrateur Coach local

Date : 2026-07-10
Statut : implémentation terminée, boucle réelle non lancée

## Résumé

**FAIT VÉRIFIÉ** — Le dépôt existant a été inspecté avant modification. Le pipeline
TikTok n'a pas été réécrit. Un orchestrateur local autour de `codex exec` a été
ajouté dans `coach_system/`, avec rôles séparés, sandboxes explicites, sorties JSON
validées, retries bornés, tests détectés, arrêt/reprise, journaux et commit local.

**RÉSULTAT DE TEST** — Les appels Codex sont entièrement mockés dans les tests. Un
dry-run d'un cycle a été exécuté sans appel Codex, sans test subprocess, sans commit
et sans modification de `coach_system/state.json`. Aucune boucle réelle autonome
n'a été lancée.

## Architecture actuelle auditée

Arbre simplifié avant l'ajout de l'orchestrateur :

```text
src/
  control_app.py                 serveur HTTP local et auto-runner
  youtube_recent_downloader.py   acquisition, découpe, rendu et pipeline
  tiktok_oauth.py                OAuth TikTok officiel
  tiktok_publisher.py            upload/publication TikTok officielle
tests/                            tests unittest
web/                              interface locale
public/                           pages publiques et callback OAuth
data/                             listes de chaînes et registre de fonctions
.state/                           états et tokens locaux, ignorés par Git
downloads/                        médias générés, ignorés par Git
videos_satisfaisantes/            sources vidéo locales
.coach/                           vision, mission, décisions et rapports
```

Points d'entrée vérifiés :

- `python src/youtube_recent_downloader.py` : CLI principale d'acquisition ;
- `python src/control_app.py` : serveur local `127.0.0.1:8787` et thread d'automation ;
- `python src/tiktok_publisher.py` : publication/upload de shorts prêts ;
- routes HTTP locales `/api/jobs`, `/api/automation`, `/api/tiktok/*` ;
- callback public `public/tiktok/callback/index.html`.

## Pipeline actuel

```text
TSV chaînes / URL manuelle
  -> résolution chaîne et détection récente (flux YouTube ou yt-dlp)
  -> téléchargement yt-dlp
  -> ffprobe durée
  -> découpe FFmpeg fixe, 60 s par défaut
  -> rendu vertical FFmpeg + vidéo satisfying locale
  -> publication/upload via API TikTok officielle si explicitement activé
  -> états JSON locaux + logs du dashboard
```

Deux chemins sont présents : traitement complet après téléchargement et chemin
streaming `clip -> rendu -> publication`. Les fonctions critiques sont
`process_download_candidate`, `process_streaming_shorts`,
`split_video_into_segments`, `render_vertical_shorts` et
`publish_rendered_shorts_to_tiktok`.

## Stockage, configuration et secrets

**FAIT VÉRIFIÉ** — Les médias vont par défaut sous `downloads/youtube` ou sous
`VIDEO_OUTPUT_ROOT`. Les états sont des JSON sous `.state/` : downloader, job,
boucle, OAuth, token et publication. `.state/`, `.env` et les téléchargements sont
ignorés par Git.

Variables identifiées sans lire ni reproduire leurs valeurs :

- vidéo/YouTube : `VIDEO_OUTPUT_ROOT`, `YOUTUBE_SINCE_HOURS`,
  `YOUTUBE_MAX_VIDEOS_PER_CHANNEL`, `YOUTUBE_DOWNLOAD_FORMAT`,
  `YOUTUBE_COOKIES_FROM_BROWSER`, `YOUTUBE_SLEEP_SECONDS` ;
- montage : `CLIP_SEGMENT_SECONDS`, `SATISFYING_VIDEO_ROOT` ;
- TikTok : `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`,
  `TIKTOK_REDIRECT_URI`, `TIKTOK_LOCAL_CALLBACK_URL`, `TIKTOK_SCOPES`,
  `TIKTOK_ACCESS_TOKEN`, `TIKTOK_AUTO_PUBLISH`, `TIKTOK_PRIVACY_LEVEL`,
  `TIKTOK_CAPTION_TEMPLATE`, `TIKTOK_PUBLISH_LIMIT` et options de publication.

## Dépendances, modèles et réseau

**FAIT VÉRIFIÉ** — La seule dépendance Python déclarée est
`yt-dlp>=2026.7.4`. Le reste du code Python utilise la bibliothèque standard.
FFmpeg/ffprobe sont des exécutables locaux requis au moment du traitement vidéo.

**FAIT VÉRIFIÉ** — Aucun modèle IA ou ML n'est actuellement intégré. Aucun appel
OpenAI, Anthropic ou Gemini n'a été trouvé ou ajouté.

Appels réseau identifiés :

- YouTube : flux XML `youtube.com/feeds/videos.xml` et extraction/téléchargement
  via yt-dlp ;
- TikTok : OAuth, révocation, informations créateur, initialisation de publication,
  upload par morceaux et état via les domaines TikTok officiels ;
- callback OAuth HTTPS public puis relais vers le serveur local.

## Orchestrateur ajouté

Fonctionnalités :

- préflight `codex`, `codex login status`, Git et propreté configurable ;
- verrou atomique anti-double-instance ;
- état persistant et reprise de `active_mission` ;
- Coach/Reviewer/Scientist `read-only`, Engineer `workspace-write` ;
- `--output-schema` et `--output-last-message` pour les sorties structurées ;
- première mission réelle forcée sur `M-001` / `AUDIT_ONLY` ;
- détection factuelle de `unittest` et d'un script npm `test` ;
- capture code, stdout, stderr et durée des tests ;
- Reviewer et Scientist avec correction jusqu'à trois tentatives ;
- blocage borné, arrêt par fichier ou Ctrl+C, journaux par cycle/tentative ;
- commit `coach(M-XXX): description`, sans aucun push ;
- mode `--dry-run` déterministe sans processus Codex.

## Fichiers créés

- `START_COACH.bat`, `STOP_COACH.bat` ;
- `coach_system/__init__.py`, `supervisor.py`, `config.json`, `state.json`,
  `README.md` ;
- `coach_system/prompts/{coach,engineer,reviewer,scientist}.md` ;
- `coach_system/schemas/{mission,review,scientist}.schema.json` ;
- `coach_system/logs/.gitkeep` ;
- `tests/test_coach_supervisor.py`.

Fichiers modifiés : `.gitignore`, `.coach/CODEX_REPORT.md`.

## Tests et résultats exacts

Baseline avant modification :

- commande : `python -m unittest discover -s tests -v` ;
- résultat : 29 tests, tous réussis, 1,943 s, exit code 0.

Validation ciblée :

- commande : `python -m unittest tests.test_coach_supervisor -v` ;
- résultat : 12 tests, tous réussis, 0,036 s, exit code 0.

Validation finale complète :

- commande : `python -m unittest discover -s tests -q` ;
- résultat : 41 tests, tous réussis, 0,459 s, exit code 0.

Les scénarios couvrent : Codex absent, dépôt non Git, JSON invalide, timeout agent,
commande de test en échec, Reviewer REJECT, Scientist INSUFFICIENT_EVIDENCE,
limite de tentatives, max cycles, stop file, reprise après interruption et verrou.

Dry-run réel du point d'entrée Windows :

- commande : `START_COACH.bat --dry-run --max-cycles 1` ;
- résultat : exit code 0 ; un cycle M-001 simulé accepté ;
- durée mesurée : 0,160 s ;
- `state.json` : hash SHA-256 inchangé avant/après ;
- artefacts créés uniquement sous le dossier de logs ignoré par Git.

## Benchmark

**LIMITATION** — Aucun traitement vidéo, modèle local, CPU/GPU intensif ou agent
Codex réel n'a été lancé ; RAM, VRAM et débit vidéo ne sont donc pas applicables à
cette mission. Le seul temps utile mesuré est le dry-run de 0,160 s. Environnement :
Windows, Python 3.13.5, Codex CLI 0.144.1.

## Risques et dettes techniques

- le downloader concentre acquisition, découpe, rendu, publication et état dans un
  fichier volumineux : risque de régression élevé lors d'une future insertion ;
- la découpe fixe précède toute analyse objective du contenu ;
- états JSON multiples et écritures fréquentes, sans verrou interprocessus commun ;
- dépendance aux formats/réponses de YouTube, yt-dlp et TikTok ;
- publication et nettoyage de fichiers sont des opérations à fort impact ;
- l'auto-runner du dashboard et le futur Coach autonome devront rester exclusifs ;
- les tests existants mockent l'essentiel des frontières externes, mais il n'existe
  pas encore de fixture vidéo d'intégration ni benchmark FFmpeg reproductible.

## Point d'intégration proposé pour le futur moteur

**PROPOSITION** — Insérer ultérieurement une couche séparée après téléchargement
et probe de durée, avant `split_video_into_segments` / `create_video_segment` :

```text
vidéo source
  -> extraction locale
  -> mesures brutes persistées
  -> normalisation
  -> sélection de fenêtres candidates
  -> rendu/publication existants
```

Réutiliser le téléchargement, FFmpeg, le rendu vertical, la publication et les
fonctions d'état. Ne pas intégrer les métriques dans une fonction de score opaque.

## Recommandations

P0 :

- faire relire puis committer manuellement l'orchestrateur avant tout lancement ;
- lancer le premier cycle réel avec `START_COACH.bat --max-cycles 1` ;
- vérifier que le livrable de M-001 reste un audit et ne publie rien sur TikTok.

P1 :

- définir le schéma persistant des mesures brutes et les frontières du futur module ;
- ajouter une petite fixture vidéo locale et un benchmark FFmpeg reproductible ;
- élargir la détection de tests seulement lorsqu'un nouveau runner est réellement
  configuré dans le dépôt.

P2 :

- séparer progressivement les responsabilités du downloader sans réécriture globale ;
- ajouter des locks/écritures atomiques aux états métier si plusieurs processus
  doivent les partager.

## Hypothèses, limitations et problèmes non résolus

**HYPOTHÈSE** — `max_retries_per_mission: 3` est interprété comme trois tentatives
totales par mission, conformément à « Maximum 3 tentatives » dans la demande.

**LIMITATION** — L'arrêt est coopératif : un agent déjà lancé termine ou atteint
son timeout. Le masquage de secrets est une défense en profondeur et non une preuve
formelle. Le validateur Python couvre le sous-ensemble JSON utilisé par les schémas ;
Codex CLI applique en plus le schéma complet pendant une exécution réelle.

**PROBLÈME NON RÉSOLU** — Aucune exécution réelle de `codex exec`, aucun commit
automatique et aucune correction autonome n'ont été validés de bout en bout, par
interdiction explicite de lancer la boucle réelle pendant cette mission.

Question bloquante : aucune pour le dry-run. Une validation explicite de Tim reste
nécessaire avant le premier cycle réel autonome.

---

## Correctif Windows encodage subprocess — 2026-07-10

### Résumé

**FAIT VÉRIFIÉ** — Le premier appel réel du Coach échouait lors du décodage de la
sortie de `codex exec` avec l'encodage Windows courant, puis la journalisation
concaténait directement `stdout` et `stderr` sans accepter `None`.

Correction ciblée :

- toutes les captures subprocess du superviseur imposent maintenant
  `encoding="utf-8"` et `errors="replace"` ;
- `normalize_process_output` transforme systématiquement les flux absents en
  chaînes vides avant toute lecture, concaténation ou journalisation ;
- la concaténation fautive utilise désormais `stdout_text` et `stderr_text` ;
- `START_COACH.bat` active la page de codes 65001, `PYTHONUTF8=1` et
  `PYTHONIOENCODING=utf-8` ;
- aucun changement n'a été apporté au pipeline TikTok ou aux métriques vidéo.

### Fichiers modifiés

- `coach_system/supervisor.py` ;
- `tests/test_coach_supervisor.py` ;
- `START_COACH.bat` ;
- `coach_system/README.md` ;
- `.coach/CODEX_REPORT.md`.

### Tests ajoutés

Neuf tests mockés, sans appel Codex réel, couvrent :

- octets UTF-8 contenant une séquence impossible à décoder en cp1252 ;
- français accentué ;
- tiret long ;
- flèche Unicode ;
- emoji ;
- `stdout=None` et `stderr=None` ;
- stdout valide avec `stderr=None` ;
- `stdout=None` avec stderr valide ;
- wrapper générique `run_checked` en UTF-8/remplacement et normalisation de `None`.

### Résultats exacts

- baseline avant correctif : 41 tests réussis en 0,672 s ;
- tests ciblés après correctif : 21 tests réussis en 0,116 s ;
- suite complète finale : 50 tests réussis en 0,487 s, exit code 0 ;
- `python -m compileall coach_system` : exit code 0 ;
- `git diff --check` : exit code 0 ;
- `START_COACH.bat --dry-run --max-cycles 1` : exit code 0 ;
- aucun cycle Codex réel, push Git ou publication TikTok exécuté.

### Limites

**LIMITATION** — `errors="replace"` conserve la continuité d'exécution mais remplace
par `�` un octet réellement invalide en UTF-8. Les sorties UTF-8 valides, y compris
accents, tiret long, flèche et emoji, sont préservées exactement. Le chemin réel
`codex exec` n'a pas été relancé conformément à l'interdiction de cette mission ;
la régression est reproduite avec un runner Codex mocké qui vérifie les paramètres
de décodage transmis par le superviseur.

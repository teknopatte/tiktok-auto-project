# Orchestrateur Coach local

Ce dossier contient un superviseur Python standard-library qui orchestre des
processus `codex exec` distincts. Il ne remplace ni ne modifie le pipeline TikTok.

## Sécurité et garanties

- authentification Codex/ChatGPT locale existante via `codex login status` ;
- aucune clé API lue ou configurée par le superviseur ;
- Coach, Reviewer et Scientist en sandbox `read-only` ;
- Engineer en sandbox `workspace-write` ;
- aucun `--yolo`, aucun `danger-full-access`, aucun push Git ;
- premier cycle réel forcé sur `M-001` / `AUDIT_ONLY` ;
- verrou anti-double-instance et arrêt coopératif ;
- maximum de tentatives borné par `max_retries_per_mission` ;
- sorties structurées validées par les schémas JSON ;
- valeurs ressemblant à des secrets masquées dans les journaux.

Sous Windows, `START_COACH.bat` active la page de codes UTF-8 et définit
`PYTHONUTF8=1` ainsi que `PYTHONIOENCODING=utf-8`. Le superviseur impose en plus
`encoding="utf-8"` et `errors="replace"` à toutes ses captures subprocess, puis
normalise les éventuels `stdout`/`stderr` absents avant de les journaliser.

Le mode réel exige par défaut un arbre Git propre, hormis `coach_system/state.json`
qui est l'état de reprise géré par le superviseur. Cette exigence est suspendue
uniquement pour reprendre une `active_mission` interrompue, dont les changements
partiels doivent rester disponibles. Le commit automatique est local et utilise
`coach(M-XXX): description`. Il n'exécute jamais `git push`.

## Validation sans appel Codex

Depuis la racine du dépôt :

```bat
START_COACH.bat --dry-run --max-cycles 1
```

Le dry-run vérifie Git, détecte les commandes de test, simule les quatre rôles,
ne lance aucun agent, n'exécute aucun test, ne modifie pas `state.json` et ne crée
aucun commit. Les simulations sont explicitement marquées dans les artefacts.

Tests unitaires mockés :

```powershell
python -m unittest tests.test_coach_supervisor -v
```

Suite complète du dépôt :

```powershell
python -m unittest discover -s tests -v
```

## Lancement réel

Après revue et commit manuel de l'orchestrateur :

```bat
START_COACH.bat
```

La configuration par défaut autorise cinq cycles. Pour une première exécution
réelle prudente :

```bat
START_COACH.bat --max-cycles 1
```

Le superviseur vérifie Codex, la connexion ChatGPT, Git et la propreté du dépôt.
Il reprend `active_mission` après une interruption. En absence de mission active,
le Coach choisit une seule mission. L'Engineer travaille, les tests détectés sont
exécutés, puis Reviewer et éventuellement Scientist statuent. Un refus est renvoyé
à l'Engineer dans la limite configurée.

`tests.json` est explicitement étiqueté `SUPERVISOR_AUTHORITATIVE_TEST_RUN` et
constitue l'unique résultat de test autoritaire pour l'acceptation. Un test lancé
par l'Engineer reste un `ENGINEER_SELF_TEST_RUN` distinct : ses timings ne sont pas
comparés à ceux du superviseur.

Avant la mission puis avant chaque tentative, le superviseur enregistre le commit
Git, le status et les empreintes SHA-256 des fichiers métier. Le Reviewer reçoit le
diff métier cumulatif et le delta de la tentative. Seuls les chemins runtime
explicites (`state.json`, logs, lock et signal d'arrêt) sont exclus ; toute vraie
modification de code reste visible. Un rejet classé `SUPERVISOR_INFRASTRUCTURE` ou
`EXTERNAL_BLOCKER` arrête la mission sans épuiser les retries Engineer.

## Arrêt

Dans un autre terminal :

```bat
STOP_COACH.bat
```

Le fichier `coach_system/STOP_REQUESTED` est vérifié avant chaque mission, avant
chaque agent et après chaque cycle. `Ctrl+C` demande le même arrêt coopératif. Un
agent déjà lancé est autorisé à terminer jusqu'à son timeout afin de préserver un
état cohérent. `START_COACH.bat` efface un ancien signal avant de démarrer.

## Tests détectés

Le superviseur n'invente pas de commande : il reconnaît actuellement les tests
Python `unittest` réellement présents sous `tests/test_*.py`, et le script `test`
d'un éventuel `package.json`. Il capture commande, code de sortie, stdout, stderr
et durée. Si rien n'est configuré, l'absence est enregistrée explicitement.

## Journaux et état

Chaque lancement crée `coach_system/logs/<horodatage>/cycle_NNN/`, puis un dossier
`attempt_NN/` par tentative. Les artefacts comprennent `coach_output.json`,
`engineer_output.txt`, `tests.json`, `reviewer_output.json`, l'éventuel
`scientist_output.json`, `mission_baseline.json`, `baseline.json`,
`post_engineer.json`, `git_diff.patch`, `attempt_diff.patch` et
`cycle_summary.json`. Cette structure évite d'écraser les sorties lors d'un
lancement multi-cycle.

`state.json` conserve les missions terminées/bloquées et la mission active.
Les journaux, le verrou et le signal d'arrêt sont ignorés par Git.

## Limites connues

- l'arrêt est coopératif : il ne tue pas brutalement un `codex exec` en cours ;
- le masquage de secrets est une défense complémentaire, pas une preuve formelle
  qu'un agent ne peut jamais produire une donnée sensible ;
- seuls `unittest` et un script npm `test` sont détectés automatiquement à ce jour ;
- le validateur Python couvre le sous-ensemble des schémas utilisés ici ; Codex CLI
  applique aussi `--output-schema` aux sorties structurées réelles ;
- les résultats scientifiques restent une revue de conformité, jamais un score de
  viralité subjectif.

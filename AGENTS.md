# AGENTS.md — Règles permanentes du projet

## 1. Hiérarchie des rôles

Ce projet fonctionne avec trois rôles :

1. Tim = propriétaire du projet et décideur final.
2. Coach = responsable stratégique, produit, architecture et contrôle qualité.
3. Codex = ingénieur principal chargé d'analyser, implémenter, tester, documenter et rendre compte.

Codex ne doit pas remplacer le Coach sur les décisions stratégiques majeures.

---

## 2. Objectif global du projet

Transformer le programme existant de publication automatique TikTok en une plateforme locale, mesurable et progressivement auto-optimisée.

Objectifs :

- sélectionner les meilleurs passages vidéo ;
- améliorer les performances réelles des vidéos ;
- maximiser les vues qualifiées ;
- améliorer la rétention ;
- améliorer la complétion ;
- améliorer les replays ;
- améliorer les partages ;
- améliorer les commentaires ;
- améliorer les abonnements générés ;
- améliorer le potentiel de rémunération ;
- conserver une architecture robuste, testable et explicable.

---

## 3. Contrainte absolue : fonctionnement local

Par défaut, aucune nouvelle fonctionnalité ne doit nécessiter :

- abonnement IA supplémentaire ;
- API IA payante ;
- OpenAI API payante ;
- Anthropic API ;
- Gemini API payante ;
- service cloud IA obligatoire ;
- dépendance SaaS payante.

Priorité obligatoire :

1. calcul algorithmique classique local ;
2. bibliothèque open source locale ;
3. modèle IA open source exécuté localement ;
4. autre solution uniquement après validation explicite de Tim.

Exemples possibles :

- FFmpeg ;
- OpenCV ;
- NumPy ;
- SciPy ;
- librosa ;
- Whisper local ;
- faster-whisper local ;
- llama.cpp ;
- modèles Hugging Face locaux ;
- embeddings locaux ;
- scikit-learn ;
- LightGBM ;
- XGBoost.

Ne jamais ajouter silencieusement une API payante.

---

## 4. Interdiction des scores IA subjectifs

Il est interdit d'utiliser une méthode du type :

"Demande à une IA si ce passage est viral et donne une note sur 100."

Interdit :

- note d'intérêt donnée librement par un LLM ;
- score viral subjectif ;
- score émotionnel inventé ;
- note de qualité sans définition mesurable ;
- coefficient arbitraire présenté comme un fait.

Un LLM local peut uniquement servir à extraire ou structurer des informations lorsque cela est nécessaire.

Exemple autorisé :

{
  "persons": 3,
  "locations": 2,
  "events": 7,
  "questions": 4
}

Le score final doit ensuite être calculé par du code déterministe ou par un modèle statistique entraîné sur des performances réelles.

---

## 5. Chaque métrique doit être explicable

Toute métrique ajoutée doit avoir :

- un nom unique ;
- une définition exacte ;
- une formule ;
- une unité ;
- une plage attendue ;
- une méthode de calcul ;
- les dépendances utilisées ;
- au moins un test ;
- les limites connues.

Exemple :

Metric:
silence_ratio

Definition:
durée totale classifiée comme silence divisée par durée totale du passage.

Formula:
silence_ratio = total_silence_seconds / clip_duration_seconds

Unit:
ratio [0, 1]

---

## 6. Séparer mesures et score

Architecture obligatoire :

VIDEO
↓
EXTRACTION
↓
MESURES BRUTES
↓
NORMALISATION
↓
MODÈLE DE SCORING
↓
RÉSULTAT EXPLICABLE

Ne jamais mélanger la détection brute et le score final dans une seule fonction opaque.

---

## 7. Deux familles de score

### IntrinsicScore

Basé sur les propriétés mesurables du passage :

- silence ;
- parole ;
- débit ;
- mouvement ;
- changements de plan ;
- visages ;
- nouveauté ;
- répétition ;
- dépendance au contexte ;
- autres métriques validées.

### AccountFitScore

Basé sur les performances historiques réelles du compte TikTok concerné.

À terme, le système doit privilégier l'apprentissage statistique local à partir des données réelles plutôt que des coefficients inventés.

---

## 8. Données brutes obligatoires

Conserver autant que raisonnablement possible les mesures brutes.

Exemple :

{
  "clip_id": "abc123",
  "start": 842.6,
  "end": 918.3,
  "duration": 75.7,
  "silence_ratio": 0.084,
  "longest_silence": 1.21,
  "speech_density": 0.892,
  "words_per_minute": 164.3,
  "face_visible_ratio": 0.81,
  "motion_mean": 0.37
}

Ne pas conserver uniquement :

{
  "score": 87
}

---

## 9. Ne pas casser l'existant

Avant toute modification importante :

1. inspecter l'architecture existante ;
2. identifier le pipeline actuel ;
3. identifier les dépendances ;
4. identifier les points d'entrée ;
5. identifier les tests existants ;
6. établir une baseline ;
7. limiter les changements non nécessaires.

Ne pas réécrire tout le projet sans justification.

---

## 10. Tests obligatoires

Toute fonctionnalité importante doit être testée.

Prévoir selon le cas :

- tests unitaires ;
- tests d'intégration ;
- fichiers fixtures ;
- tests de non-régression ;
- cas limites ;
- entrées invalides.

Un résultat "ça devrait marcher" n'est pas suffisant.

---

## 11. Benchmark obligatoire

Pour les traitements coûteux :

- mesurer le temps ;
- mesurer la RAM ;
- mesurer si possible la VRAM ;
- identifier CPU ou GPU ;
- enregistrer la version du modèle ;
- enregistrer les paramètres.

Le projet doit rester utilisable localement.

---

## 12. Transparence

Codex doit distinguer clairement :

- FAIT VÉRIFIÉ ;
- RÉSULTAT DE TEST ;
- HYPOTHÈSE ;
- PROPOSITION ;
- LIMITATION ;
- PROBLÈME NON RÉSOLU.

Ne jamais présenter une hypothèse comme une certitude.

---

## 13. Protocole Coach

Avant chaque mission importante, lire :

.coach/PROJECT_VISION.md
.coach/CURRENT_MISSION.md
.coach/COACH_REVIEW.md
.coach/DECISIONS.md

Après chaque mission importante, mettre à jour :

.coach/CODEX_REPORT.md

---

## 14. Rapport obligatoire

À la fin d'une mission, CODEX_REPORT.md doit indiquer :

- résumé ;
- fichiers modifiés ;
- fonctionnalités ajoutées ;
- tests lancés ;
- résultats exacts ;
- benchmarks ;
- décisions techniques ;
- hypothèses ;
- problèmes ;
- limitations ;
- prochaines propositions.

---

## 15. Priorité actuelle

La priorité stratégique principale est le moteur local et objectif de sélection automatique des meilleurs passages d'une vidéo longue.

Il doit être :

- local ;
- mesurable ;
- reproductible ;
- explicable ;
- testable ;
- progressivement améliorable avec les performances réelles TikTok.

---

## 16. Règle finale

Quand une instruction est ambiguë :

- inspecter d'abord le projet ;
- préférer la solution la plus testable ;
- faire l'hypothèse minimale ;
- documenter l'hypothèse ;
- ne jamais inventer de résultats.

Les décisions explicites de Tim ont priorité.
Les missions du Coach définissent la direction stratégique.
Codex est responsable de la qualité technique de l'implémentation.
# CURRENT MISSION

Mission ID: M-001
Priority: P0
Type: AUDIT ONLY

## Objectif

Auditer intégralement le projet existant avant toute implémentation du moteur de sélection vidéo.

## Important

Cette mission est principalement une mission d'analyse.

Ne pas commencer à développer le moteur complet.
Ne pas réécrire l'architecture.
Ne pas remplacer le pipeline existant.
Ne pas ajouter de nouvelle API payante.

## Travail demandé

1. Explorer l'intégralité du repository.
2. Identifier tous les points d'entrée.
3. Identifier le flux complet actuel :
   - acquisition vidéo ;
   - téléchargement ;
   - traitement ;
   - montage ;
   - sous-titres ;
   - publication TikTok ;
   - stockage ;
   - logs.
4. Identifier toutes les dépendances.
5. Identifier les modèles IA existants.
6. Identifier les appels réseau.
7. Identifier les API externes.
8. Identifier les composants déjà locaux.
9. Identifier la structure de configuration.
10. Identifier les secrets et variables d'environnement sans afficher leurs valeurs.
11. Identifier les tests existants.
12. Identifier les risques de régression.
13. Identifier où intégrer proprement le futur moteur de sélection.
14. Identifier les parties réutilisables.
15. Identifier les dettes techniques pertinentes.

## Livrable obligatoire

Mettre à jour :

.coach/CODEX_REPORT.md

Le rapport doit contenir :

- architecture actuelle ;
- pipeline actuel ;
- arbre simplifié du projet ;
- composants critiques ;
- dépendances ;
- risques ;
- points d'intégration proposés ;
- éléments manquants ;
- questions réellement bloquantes ;
- recommandations classées P0/P1/P2.

## Interdiction

Ne pas implémenter le moteur complet pendant M-001.

De petites corrections ne sont permises que si elles sont strictement nécessaires pour terminer l'audit, et elles doivent être documentées.
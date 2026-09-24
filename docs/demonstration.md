# Préparer une démonstration

Deux façons d'avoir un catalogue crédible devant un public : de vraies
bibliothèques Zotero (le mieux : c'est le parcours réel), ou des données
de démonstration installées en une commande (le plus rapide). Les deux
se combinent.

## 1. De vraies bibliothèques Zotero

Pour chaque université de la démonstration :

1. Sur zotero.org, créer un **groupe** (par exemple « BU UCAD — ScholarSync »),
   privé, et y inviter le compte qui catalogue.
2. Dans ce groupe, créer à la racine les collections **Thèses** et
   **Mémoires**. Sous chacune, une sous-collection par école doctorale ou
   faculté (« École doctorale ETHOS », « FASEG »…) : elles deviennent la
   facette « École doctorale / Faculté » du portail.
3. Saisir quelques notices de type **Thèse** : titre, auteur, date, résumé,
   directeur (ajouté comme « Contributeur » dans Zotero), URL du texte
   intégral si elle existe.
4. Ajouter des **marqueurs** (tags) :
   - `statut: soutenu` ou `statut: en_preparation` (sans marqueur : soutenu) ;
   - `domaine: Économie`, `domaine: Droit`… (facette « Domaine ») ;
   - les autres marqueurs deviennent des mots-clés.
5. Créer une **clé API** en lecture seule sur le groupe (zotero.org →
   Paramètres → Sécurité → Clés).
6. Dans ScholarSync, connecté avec le compte de l'université :
   **Mon établissement → Source Zotero**, renseigner l'identifiant du groupe
   et la clé, « Tester la connexion », « Enregistrer », puis
   **Synchroniser**.

L'identifiant du groupe est le nombre dans l'adresse
`https://www.zotero.org/groups/5123456/…`.

## 2. Données de démonstration

Une soixantaine de notices fictives réparties entre UCAD, UGB et UADB
(créées si elles n'existent pas), leurs facultés et écoles doctorales,
des domaines variés, de 2015 à 2025, dont quelques travaux en préparation :

```bash
docker exec scholarsync-app python -m app.outils.demo installer
docker exec scholarsync-app python -m app.outils.demo etat
```

Chaque notice porte « Notice de démonstration » en tête de son résumé.
Après la présentation, sur une instance de production :

```bash
docker exec scholarsync-app python -m app.outils.demo retirer
```

Les notices réelles ne sont pas touchées. Les numéros nationaux attribués
aux notices de démonstration restent consignés dans « Documents retirés »
et ne seront pas réattribués. Pour une démonstration sans aucune trace en
production, utilisez une instance à part (autre serveur, ou copie du dossier
avec un autre `container_name` et un autre port).

## 3. Comptes pour la démonstration

Dans **Utilisateurs** (super administrateur), créer :

- un compte **Administrateur d'établissement** rattaché à UCAD ;
- un compte **Lecteur** rattaché à UGB (pour montrer la consultation seule).

Ouvrir chaque compte dans une fenêtre de navigation privée différente : on
passe d'un rôle à l'autre sans se déconnecter.

## 4. Vérifications la veille

- [ ] Le portail s'ouvre, en FR, EN et PT.
- [ ] La recherche tolère une faute (« gouvernence ») : Meilisearch répond
      (`docker compose logs scholarsync-app | grep -i meilisearch`).
- [ ] Une synchronisation manuelle aboutit (page Synchroniser, historique).
- [ ] Les logos des universités s'affichent (liste Établissements).
- [ ] Le rapport PDF se télécharge (tableau de bord → Rapport PDF).
- [ ] Une notice test ajoutée dans Zotero arrive sur le portail avec son
      numéro national (et une notice « en préparation » sans numéro).
- [ ] Captures d'écran ou courte vidéo du parcours, au cas où le réseau
      ferait défaut.

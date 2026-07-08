"""report_text.py -- French glossary + narrative text for the xlsx run-off report (stdlib).

Kept separate so the workbook assembly (runoff_book.write_xlsx_report) stays about layout,
not prose. All ALM-facing text is in FRENCH (the deliverable audience). A(t)/r(t)/B(t) are
the three curves the report is built around; the glossary is the report's first sheet.
"""
from __future__ import annotations

# (Terme, Definition) -- rendered as the 'Glossaire' sheet (2 columns, bold header).
GLOSSAIRE = [
    ("A(t) - Survie / attrition",
     "Proportion des COMPTES encore ouverts au mois t parmi ceux presents a t=0 "
     "(t=0 -> A=1). Estimee par un modele de survie a hasard discret (logistique "
     "elastic-net) : A(t) = produit sur h<=t de (1 - taux de cloture du mois h). "
     "Ne tient compte que des fermetures de comptes, PAS de la variation des soldes."),
    ("r(t) - Retention du solde (erosion)",
     "Solde moyen restant par compte SURVIVANT au mois t, rapporte au solde de t=0 "
     "(t=0 -> r=1). Estime sur les increments mensuels de log-solde (regression "
     "elastic-net, perte de Huber robuste aux valeurs extremes). r(t)<1 = erosion du "
     "solde des comptes qui restent ouverts."),
    ("B(t) - Ecoulement du livre (run-off)",
     "Courbe DEPLOYEE, la reponse IRRBB : fraction du SOLDE TOTAL du livre encore "
     "presente au mois t. B(t) = A(t) x r(t) (attrition x erosion), t=0 -> B=1, "
     "decroit vers 0. Colonne mise en VERT dans les tableaux."),
    ("Ecoulement complet (jusqu'a 0)",
     "L'horizon complet est de 360 mois (30 ans, standard IRRBB). 'Run-off a <0,5%' = "
     "le mois ou B(t) passe sous 0,5% du solde initial (fin pratique de l'ecoulement)."),
    ("WAL - Duree de vie moyenne ponderee",
     "Weighted Average Life = somme de B(t) sur l'horizon (en mois). Mesure combien de "
     "temps, en moyenne, le solde reste dans le livre. WAL_base vs WAL_+200bp = "
     "sensibilite au choc de taux."),
    ("Choc +200bp",
     "Scenario de stress reglementaire : +2,00% (200 points de base) applique au taux "
     "de marche monetaire, propage dans le hasard et l'erosion -> B(t) sous stress. "
     "Le signe de l'effet depend du coefficient estime par livre."),
    ("Livre comportemental (segment)",
     "Produit sans echeance contractuelle modelise par le COMPORTEMENT (pas un "
     "echeancier) : comptes a vue dinars/devises, epargne, decouverts, engagement de "
     "financement HB. Chaque livre a son propre A(t), r(t), B(t) ; le livre global est "
     "la somme ponderee par les soldes."),
    ("Livre contractuel (exclu)",
     "Produits a echeance (DAT, BDC, credits, depots de garantie). L'ecoulement vient "
     "d'un echeancier (dates d'echeance), absent de l'EFM (photo de stock). Non "
     "modelise ici -> tague et exclu (voir feuille Skipped)."),
    ("Ponderation du livre",
     "B_livre(t) = somme sur les livres s de W_s x B_s(t) / somme des W_s, ou W_s est "
     "le solde courant du livre s. Le livre global est donc domine par les plus gros "
     "livres (poids en %)."),
    ("Regime (HMM)",
     "Etat macro-financier latent (HMM gaussien, nombre d'etats choisi par BIC) filtre "
     "de facon causale sur les series macro (inflation, taux, petrole). Sert de "
     "variable au hasard s'il ameliore la validation (Gate B). Feuille 'Regime'."),
    ("Surveillance de rupture (CUSUM)",
     "Detecteur de rupture de moyenne (CUSUM) sur le taux de marche monetaire. Une "
     "alarme recente RECOMMANDE un re-calibrage anticipe (elle ne modifie jamais le "
     "modele seule). Feuille 'Surveillance'."),
    ("Selection train / validation / test",
     "Protocole imbrique sans biais : hyper-parametres choisis sur TRAIN (walk-forward), "
     "famille de variables et modele d'ecoulement choisis sur VALIDATION, tete de serie "
     "de TEST touchee UNE fois = chiffre hors-echantillon honnete (test_NLL)."),
    ("Scores (PAS des p-values)",
     "NLL et Brier sont des SCORES propres (pas des p-values, pas des statistiques brutes "
     "a lire seules). Pour les rendre lisibles, la feuille Training donne le SKILL % = "
     "amelioration vs le modele naif base-rate (predit le taux moyen d'evenement pour "
     "tous) : skill = 1 - score/score_naif ; >0 = meilleur que naif, 100% = parfait."),
    ("ECE + PIT-KS (calibration)",
     "ECE (Expected Calibration Error) = erreur de calibration en points de proba "
     "(0 = parfait ; seuil 'ok' < 1%). PIT-KS = statistique de Kolmogorov-Smirnov de "
     "l'histogramme PIT vs uniforme ; on donne sa P-VALUE : p>5% => on ne rejette pas "
     "l'uniformite => calibre (colonne 'calibre?'). C'est la seule vraie p-value du rapport."),
    ("dEVE (feuille IRRBB)",
     "Delta Economic Value of Equity : variation de la valeur economique des fonds propres "
     "sous choc de taux. On actualise l'ECOULEMENT des depots (un passif) ; hausse de taux "
     "=> PV du passif baisse => valeur des fonds propres MONTE (dEVE>0 = favorable). Le "
     "risque LIANT = le scenario le plus NEGATIF. Gestion du risque : on lit le PIRE, pas "
     "le +200bp favorable. Ici le pire = -200bp (les depots sont un financement long)."),
    ("dNII (feuille IRRBB)",
     "Delta Net Interest Income sur 1 an : variation de la marge d'interet. Le cout des "
     "depots monte de beta x choc sur la part qui se reprice dans l'annee (beta = "
     "pass-through au taux client ; ~0 pour les comptes a vue non remuneres, >0 pour "
     "l'epargne)."),
    ("6 scenarios EBA + parallele",
     "Chocs prescrits (BCBS/EBA), PAS un forecast : parallele +/-, court +/-, "
     "pentification, aplatissement, + les deux paralleles +/-200bp. Le run-off "
     "reglementaire est CONDITIONNE a ces scenarios, jamais pilote par une prevision maison."),
    ("Regimes (Calme/Stress)",
     "Les etats du HMM sont ETIQUETES a partir de leurs moyennes macro (inflation, taux, "
     "petrole) : 'Calme (oil haut)' vs 'Stress (oil bas)'. On n'affiche jamais 'regime 0/1'. "
     "Le HMM = detecteur de regime, PAS un bon generateur (queues fines) => la simulation "
     "utilise un AR regime-switch + Student-t, et la CRISE est un overlay IMPOSE."),
    ("Crise imposee + positionnement",
     "Aucun modele fitte ne peut apprendre une crise absente des donnees. La SEVERITE "
     "(chute oil, elasticite de fuite des depots) est une HYPOTHESE (a caler sur 2014-16), "
     "pas une estimation. Feuille Crise_Stress = reverse stress impose + bande de "
     "positionnement (macro generatif). Vue ECONOMIQUE (couverture), NON reglementaire."),
    ("Incertitude parametrique",
     "Sur ~120 mois, l'incertitude qui compte n'est pas la trajectoire macro mais les "
     "COEFFICIENTS. Bootstrap par blocs temporels -> re-estimation -> bande de B(t) + IC "
     "sur la WAL (feuille Incertitude)."),
    ("convention / ECM / hasard",
     "Les 3 modeles d'ecoulement compares hors-echantillon : convention (noyau/volatil, "
     "reference reglementaire), ECM (elasticite au taux, economie), hasard (A(t)xr(t), "
     "comportemental). Le gagnant par livre est reporte ; les courbes affichees sont le "
     "modele comportemental."),
]

# VBA reference (TEXT ONLY). We ship NATIVE .xlsx charts (they render on open, no macro),
# because pure stdlib cannot write the OLE2 vbaProject.bin an .xlsm needs AND locked-down
# bank PCs block macros. This is provided so a power user who WANTS macros can Alt+F11 ->
# Import a .bas / paste this, e.g. to rebuild a chart or refresh from the DATA sheets.
VBA_REFERENCE = r'''Attribute VB_Name = "RunoffCharts"
' Reference macros for the run-off report. NOT required to view the report: the charts
' are already embedded as native xlsx chart objects. Use only if you want to regenerate.
' Import: Excel > Alt+F11 (VBE) > File > Import File... (save this as RunoffCharts.bas),
' or paste into a new Module. Then run BuildRunoffChart.
Option Explicit

' Rebuild a line chart of B(t)/A(t) from a curve sheet laid out as:
'   A=mois_h, B=A_t, C=r_t, D=B_t, E=B_t_+200bp  (header in row 1)
Sub BuildRunoffChart(Optional sheetName As String = "Ecoulement_Livre")
    Dim ws As Worksheet, ch As ChartObject, n As Long
    Set ws = ThisWorkbook.Worksheets(sheetName)
    n = ws.Cells(ws.Rows.Count, 1).End(xlUp).Row      ' last data row
    Set ch = ws.ChartObjects.Add(Left:=ws.Range("G2").Left, Top:=ws.Range("G2").Top, _
                                 Width:=520, Height:=300)
    With ch.Chart
        .ChartType = xlLine
        .SetSourceData Source:=ws.Range("A1:A" & n & ",B1:B" & n & ",D1:D" & n & ",E1:E" & n)
        .HasTitle = True
        .ChartTitle.Text = "Ecoulement du livre B(t)"
    End With
End Sub

' Colour the B(t) column (D) green on every curve sheet (Courbe_* and Ecoulement_Livre).
Sub GreenifyBt()
    Dim ws As Worksheet, n As Long
    For Each ws In ThisWorkbook.Worksheets
        If ws.Name = "Ecoulement_Livre" Or Left$(ws.Name, 7) = "Courbe_" Then
            n = ws.Cells(ws.Rows.Count, 4).End(xlUp).Row
            If n > 1 Then ws.Range("D2:D" & n).Interior.Color = RGB(99, 190, 123)
        End If
    Next ws
End Sub
'''

# Guide des feuilles : pour CHAQUE feuille, en francais simple -> c'est quoi + a regarder.
# (Feuille, C'est quoi, A regarder / comment l'utiliser)
SHEET_GUIDE = [
    ("Glossaire", "Definition des termes (A(t), r(t), B(t), WAL, dEVE, regime...).",
     "A lire en premier pour comprendre le vocabulaire."),
    ("Guide", "Cette feuille : a quoi sert chaque feuille du classeur.",
     "Votre table des matieres."),
    ("Synthese", "Vue d'ensemble : chaque livre (solde, poids %, duree de vie WAL) + le "
     "livre GLOBAL (ligne BOOK).",
     "Regardez la WAL (en mois) et le poids de chaque livre. La colonne WAL est en VERT."),
    ("Ecoulement_Livre", "La courbe d'ECOULEMENT du livre global : B(t) = fraction du solde "
     "encore presente au mois t, sur 30 ans. 2 graphiques (30 ans + zoom 5 ans).",
     "A quelle vitesse l'argent part. B(t) part de 1 et descend vers 0. Colonne B en VERT."),
    ("Courbe_<livre>", "Meme courbe d'ecoulement, mais pour UN livre (epargne, vue dinars...).",
     "Comparer la vitesse d'ecoulement entre livres (l'epargne s'ecoule lentement, les "
     "decouverts vite)."),
    ("Training", "Qualite du modele HORS-ECHANTILLON : skill % vs un modele naif, + calibration.",
     "Skill % > 0 = mieux que naif. Colonne 'calibre?(5%)' = 'oui' -> le modele est fiable."),
    ("Comparaison_Modeles", "Compare 3 modeles d'ecoulement (convention / ECM / hasard) sur "
     "erreur hors-echantillon.",
     "Le 'gagnant' est le modele deploye. MAE : plus bas = mieux."),
    ("HP_Surface", "Carte des hyper-parametres (technique, pour le model risk).",
     "Technique ; couleur verte = meilleur reglage."),
    ("Fiabilite", "Diagramme de fiabilite : probabilite PREDITE vs REELLE, par livre.",
     "Les points doivent suivre la diagonale (ligne y=x) = bien calibre."),
    ("PIT", "Histogramme PIT (test de calibration).",
     "Un histogramme PLAT = bien calibre. En pic = mal calibre."),
    ("IRRBB_EVE_NII", "LE RISQUE DE TAUX : variation de la valeur (dEVE) et de la marge "
     "(dNII) sous chaque scenario de choc de taux (6 scenarios EBA + -/+200bp).",
     "Regardez le PIRE scenario (dEVE le plus NEGATIF) = le risque liant. Ici c'est souvent "
     "la BAISSE de taux (les depots = financement long)."),
    ("IRRBB_par_livre", "dEVE par livre sous le pire scenario + le beta de depot.",
     "Quel livre porte le plus de risque de taux."),
    ("Challenger_GBM", "Comparaison honnete : notre modele logistique vs une IA (GBM type "
     "XGBoost), hors-echantillon.",
     "Le 'gagnant'. Sur ces donnees le logistique GAGNE (l'IA sur-apprend)."),
    ("Crise_Stress", "Choc de CRISE impose (chute petrole -> depreciation dinar -> fuite des "
     "depots). La severite est une HYPOTHESE (a caler sur 2014-16), pas une prevision.",
     "Regardez le raccourcissement de la WAL sous crise (le livre s'ecoule beaucoup plus vite)."),
    ("Crise_Bande", "Bande d'ecoulement sous stress petrole (trajectoires simulees).",
     "L'eventail p5/median/p95 = l'incertitude de la crise."),
    ("Incertitude", "Incertitude des COEFFICIENTS (bootstrap) : bande de B(t) + intervalle "
     "de confiance sur la WAL.",
     "La largeur de la bande = a quel point on est sur. IC WAL en feuille Incertitude_WAL."),
    ("Regime", "Probabilite du regime macro (Calme / Stress) dans le temps (HMM).",
     "Quand la proba 'Stress' monte = periode de tension (crise petrole/change)."),
    ("Regime_Actuel", "Le regime du DERNIER mois observe.",
     "Sommes-nous en Calme ou en Stress aujourd'hui."),
    ("Surveillance", "Detecteur de RUPTURE (CUSUM) sur le taux de marche.",
     "Si 'alarme rupture recente = OUI' -> le pipeline se re-calibre tout seul."),
    ("DATA_macro", "Toutes les donnees macro telechargees : petrole, inflation, taux, change, "
     "ramadan, PRIME PARALLELE (marche noir EUR, reelle depuis 2016) + graphiques.",
     "Le contexte economique. La prime parallele monte = stress sur le dinar."),
    ("DATA", "Les donnees clients (le panel compte-mois) qui alimentent le modele.",
     "La matiere premiere ; ne pas modifier."),
    ("Skipped", "Livres EXCLUS du modele (trop peu de donnees, ou contractuels).",
     "Verifier qu'aucun livre important n'est exclu par erreur."),
    ("VBA_Source", "Macro VBA de reference (optionnelle, pour re-generer des graphiques).",
     "Ignorer sauf si vous voulez personnaliser via Alt+F11."),
]

# Notes shown under the Book Summary (context for the ALM reader).
SUMMARY_NOTES = [
    "Toutes les valeurs proviennent de donnees SYNTHETIQUES tant que le panel reel EFM "
    "n'a pas ete fourni (demo methodologique). Sur le PC de travail, passez le panel reel.",
    "B(t) (vert) est la courbe d'ecoulement deployee = A(t) x r(t). WAL = somme de B(t).",
    "Horizon complet 360 mois (30 ans). Voir la feuille de chaque livre pour la courbe "
    "detaillee et le graphique.",
]

<div align="center">
  <img src="design/mm/app-logo.svg" alt="Signal Desk" width="72">

  # Signal Desk

  **Il tuo brief quotidiano sull'AI. Dieci minuti, non due ore.**

  Gira sul tuo computer. Nessun account, nessun abbonamento, nessun dato che esce da casa tua.

  🇮🇹 Italiano · [🇬🇧 English](README.md)
</div>

<p align="center">
  <img src="docs/images/brief-dark.png" alt="Signal Desk: il brief del giorno" width="900">
</p>

---

## Il problema

Le novità sull'AI escono ogni giorno da decine di fonti diverse: blog ufficiali dei
laboratori, release di modelli, strumenti nuovi, progetti open source. Seguirle tutte
è impossibile. Ignorarle significa restare indietro.

## La soluzione

Signal Desk raccoglie ogni giorno le notizie dalle fonti ufficiali, scarta il rumore,
le ordina per importanza e te le consegna in un **brief da leggere in pochi minuti**,
con, per ogni notizia, *perché conta* e *cosa puoi farci*.

E se scrivi due righe su di te (che lavoro fai, cosa vuoi imparare), il brief si adatta
ai tuoi interessi.

---

## Cosa trovi dentro

| | |
|---|---|
| 📰 **Brief** | Le cose che contano oggi, già sintetizzate e ordinate per rilevanza. |
| 🔍 **Esplora** | Tutti i segnali raccolti, con filtri per tema, fonte, qualità e data. |
| ⭐ **Salvati** | Metti da parte gli articoli da riprendere con calma. |
| ✉️ **Newsletter** | Genera un'edizione su misura per un periodo a tua scelta (settimana, mese…). |
| 🔬 **Approfondisci** | Un click su un articolo e l'AI te lo spiega più a fondo. |
| ✍️ **Niente riempitivi** | Se l'AI non ha ancora analizzato un articolo, l'app te lo dice e ti offre di analizzare quel singolo articolo, invece di scrivere una frase generica uguale per tutti. |
| 👤 **Profilo** | Descrivi chi sei: nasce la sezione "Per te", tarata sui tuoi interessi. |
| ⚑ **Fonti** | Una selezione di fonti ufficiali già attive dal primo giorno, su un catalogo più ampio. Aggiungi o togli quello che vuoi. |
| ◷ **Consumo AI** | Quanto stai usando l'AI, run per run. Trasparenza totale, nessuna sorpresa. |
| 🌗 **Tema chiaro/scuro** | E interfaccia in italiano o inglese, a scelta. |

<p align="center">
  <img src="docs/images/explore-light.png" alt="Esplora: tutti i segnali, con filtri" width="440">
  <img src="docs/images/sources-light.png" alt="Fonti: quelle attive e il catalogo" width="440">
</p>
<p align="center"><sub>Esplora e Fonti, in tema chiaro.</sub></p>

**Funziona anche senza AI.** Ogni funzione ha un'alternativa automatica: senza
strumenti AI collegati l'app raccoglie, filtra e ordina comunque tutto. L'AI aggiunge
le sintesi scritte bene, gli approfondimenti e la personalizzazione.

---

## Installazione

Serve solo **Python** installato sul computer. Se non ce l'hai, gli script te lo dicono
e ti danno il link: si scarica in due minuti da [python.org](https://www.python.org/downloads/).

### 🍎 Su Mac

1. Scarica il progetto ([Code → Download ZIP](https://github.com/Manfrini/signal-desk)) e aprilo.
2. Entra nella cartella `scripts` e fai **doppio clic su `start.command`**.
3. Aspetta: la prima volta prepara tutto da solo. Poi Signal Desk si apre nel browser.

> Se il Mac dice che lo script è di uno sviluppatore non identificato: clic destro sul
> file → **Apri** → **Apri**. Succede solo la prima volta.

### 🪟 Su Windows

1. Scarica il progetto ([Code → Download ZIP](https://github.com/Manfrini/signal-desk)) e aprilo.
2. Entra nella cartella `scripts`, clic destro su **`start.ps1`** → **Esegui con PowerShell**.
3. Aspetta: la prima volta prepara tutto da solo. Poi Signal Desk si apre nel browser.

> Se Windows blocca lo script, apri PowerShell nella cartella del progetto e incolla:
> ```powershell
> powershell -ExecutionPolicy Bypass -File scripts/start.ps1
> ```

### Poi?

Tutti i giorni successivi: stesso doppio clic. Tutto qui.

---

## Primo avvio: 2 minuti

Alla prima apertura una procedura guidata ti chiede tre cose:

1. **In che lingua** vuoi l'app e i contenuti.
2. **Chi sei**, in poche righe libere: serve a personalizzare il brief. Puoi saltarla.
3. Se vuoi **fare subito la prima raccolta**, così hai qualcosa da leggere.

Puoi rifiutare ogni passaggio e cambiarlo dopo dalla sezione **Profilo**.

## Vuoi anche le sintesi scritte dall'AI?

Opzionale, ma è dove Signal Desk dà il meglio. Ti serve uno di questi strumenti
(gratuito da installare; per usarlo accedi con un abbonamento Claude o ChatGPT):

- [Claude Code](https://claude.com/claude-code): `npm install -g @anthropic-ai/claude-code`
- Codex CLI: `npm install -g @openai/codex`

Poi vai in **Profilo → Impostazioni AI**, scegli quale usare e fai l'accesso
direttamente dall'app. Il consumo resta visibile nella sezione **Consumo AI**.

> **Una differenza da conoscere.** Con **Codex** l'AI può eseguire comandi in
> sola lettura sul tuo computer: è come funziona quello strumento e non si può
> disattivare. In teoria un articolo malevolo, tra quelli raccolti, potrebbe
> convincere l'AI a leggere un tuo file e mostrarne il contenuto nell'app. Con
> **Claude** non succede: la lettura di file è bloccata, l'abbiamo verificato.
>
> In pratica il rischio è basso (le fonti sono blog ufficiali che scegli tu),
> e Signal Desk delimita sempre il testo di terze parti prima di passarlo
> all'AI. Ma se vuoi l'isolamento più stretto, usa Claude; in ogni caso
> aggiungi solo fonti di cui ti fidi.

---

## Domande veloci

**I miei dati escono dal computer?**
No. Tutto (articoli, profilo, preferenze) resta in locale. Le uniche connessioni
sono verso i siti delle notizie e, se lo attivi, verso il tuo strumento AI. La
dashboard risponde solo al tuo computer e rifiuta le richieste che arrivano da
altri siti. Vedi però la nota sopra sulla differenza tra Codex e Claude.

**Costa qualcosa?**
L'app no, è open source. Se colleghi un'AI, vale il tuo piano con quel servizio: il
consumo lo vedi dentro l'app.

**Devo saper programmare?**
No. Doppio clic sullo script e l'app si apre nel browser come un qualsiasi sito.

**Ho già qualcosa che usa la porta 8000.**
Nessun problema: Signal Desk se ne accorge e passa alla prima porta libera
(8001, 8002…), scrivendolo all'avvio. Il browser si apre da solo su quella
giusta.

**Posso scegliere le fonti?**
Sì, dalla sezione **Fonti**: aggiungi, modifica o disattiva quello che vuoi, senza
toccare un file.

---

## Per sviluppatori

Comandi da terminale, architettura, formato dei dati, design system, configurazione
avanzata e workflow di sviluppo: **[docs/TECHNICAL.md](docs/TECHNICAL.md)**.
Analisi di sicurezza, modello di minaccia e limiti noti:
**[docs/SECURITY-REVIEW.md](docs/SECURITY-REVIEW.md)**.

In breve:

```bash
python -m pip install -r requirements.txt
python -m src.main setup      # procedura guidata
python -m src.main run-daily  # pipeline completa
python -m src.main serve      # dashboard su http://127.0.0.1:8000
```

## Licenza

Vedi [LICENSE](LICENSE).

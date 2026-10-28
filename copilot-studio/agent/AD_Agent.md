# Agent „Auditable Design” (Copilot Studio)

Agent jest interfejsem dla ludzi. Nie wykonuje potoku: uruchamia przebieg, podaje jego stan i odpowiada na pytania o dowody. Potok wykonuje flow `AD_Przebieg` uruchamiany plikiem w OneDrive.

Rozdzielenie wynika z dwóch rzeczy:

- **Deterministyczna kolejność kroków.** Potok musi przejść wszystkie kroki w tej samej kolejności i zapisać każdy z nich. Generatywny orkiestrator sam wybiera narzędzia ([Learn](https://learn.microsoft.com/en-us/microsoft-copilot-studio/advanced-generative-actions)), więc tej kolejności nie zagwarantuje.
- **Limit czasu.** Flow wywoływany przez agenta musi odpowiedzieć w 100 sekund, synchronicznie ([Learn](https://learn.microsoft.com/en-us/microsoft-copilot-studio/flow-agent)). Przebieg trwa dłużej.

## Ustawienia agenta

- **Nazwa:** Auditable Design
- **Orkiestracja:** generatywna (domyślna).
- **Model:** dowolny dopuszczony. Agent tylko rozmawia i wywołuje narzędzia, nie ocenia produktu.
- **Wiedza:** brak. Agent odpowiada wyłącznie na podstawie narzędzi, żeby każda odpowiedź o dowodach pochodziła z plików przebiegu.

## Instrukcje (wklej w Instructions)

```
Jesteś asystentem zespołu projektowego, który pracuje metodą Auditable Design: głos klienta przechodzi przez audyt sześcioma teoriami projektowania do kierunku projektowego, a każdy krok jest zapisany.

Twoje zadania:
1. Uruchomienie przebiegu: zbierz zestaw danych (folder w AuditableDesign/wejscie), tryb (A_od_klastrow albo B_od_wypowiedzi), listę klastrów dla trybu A i opis modelu. Wywołaj narzędzie AD_Uruchom. Podaj użytkownikowi run_id i powiedz, że przebieg trwa kilka minut.
2. Stan przebiegu: wywołaj AD_Status z run_id i przekaż etap oraz ewentualne błędy.
3. Dowody: na pytania „na czym opiera się ustalenie X”, „które recenzje to wspierają”, „czy to głos klienta czy obserwacja audytora” wywołaj AD_Dowody i przekaż wynik bez upiększania.

Zasady:
- Nie oceniaj produktu sam i nie dopowiadaj ustaleń. Mówisz tylko to, co zwróciły narzędzia.
- Zawsze rozróżniaj dwa rodzaje dowodu: ustalenie oparte na cytatach klientów (podaj identyfikatory recenzji i cytaty) i ustalenie oparte tylko na artefakcie produktu (zrzut, makieta). Drugiego nie nazywaj głosem klienta.
- Jeśli narzędzie zwróci błąd albo pusty wynik, powiedz to wprost i podaj, czego brakuje.
- Nie podejmuj decyzji projektowych za zespół. Brief to wejście do decyzji ludzi.
- Odpowiadaj po polsku, krótko.
```

## Narzędzia (agent flows z wyzwalaczem „When an agent calls the flow”)

### AD_Uruchom

- **Wejścia:** `zestaw` (tekst), `tryb` (tekst), `klastry` (tekst, lista po przecinku, opcjonalnie), `model_opis` (tekst).
- **Kroki:**
  1. `run_id` = `concat(variables('tryb_krotko'), '-', formatDateTime(utcNow(), 'yyyyMMdd-HHmmss'))`.
  2. Compose `run_request`: `{"run_id", "tryb", "zestaw", "klastry": split(klastry, ','), "model_opis"}`.
  3. OneDrive **Create file**: folder `/AuditableDesign/start`, nazwa `@{run_id}.json`, treść `string(outputs('Compose'))`.
  4. **Respond to the agent:** `run_id`.
- Zajmuje kilka sekund, więc mieści się w limicie 100 s. Właściwy przebieg uruchamia wyzwalacz OneDrive w `AD_Przebieg`.

### AD_Status

- **Wejście:** `run_id`.
- **Kroki:** OneDrive **Get file content using path** `/AuditableDesign/przebiegi/@{run_id}/status.json` → **Respond to the agent**: pola `etap`, `klaster`, `bledy`, `zakonczony`.
- Jeśli pliku nie ma: odpowiedz „przebieg jeszcze nie wystartował albo run_id jest błędny”.

### AD_Dowody

- **Wejścia:** `run_id`, `cluster_id`, `heurystyka` (opcjonalnie).
- **Kroki:**
  1. Get file content: `/AuditableDesign/przebiegi/@{run_id}/@{cluster_id}/klaster.json` oraz `.../L5_uzgodnienie.json`.
  2. **Compose** z obiektem `{"cluster": json(<treść klaster.json>), "reconciled": json(<treść L5_uzgodnienie.json>)?['wynik']?['verdict'], "heuristic": <heurystyka>}`. Ścieżka `wynik.verdict` wynika z formatu zapisu kroku (W3 w F1).
  3. Excel Online (Business) → **Run script**: skrypt `AD_Dowody`, `operation` = `dowody`, `payload` = `string(outputs('Compose'))`.
  4. **Respond to the agent:** `json(outputs('Run_script')?['body/result'])?['result']?['summary']`.
- Wynik dla każdego ustalenia: identyfikatory recenzji i cytaty albo informacja „brak cytatu klienta, ustalenie oparte tylko na artefakcie produktu”.

## Agenci podrzędni (opcjonalnie)

Dokumentacja opisuje agentów podrzędnych jako lekkich agentów w kontekście agenta głównego, z własnymi instrukcjami, narzędziami i wiedzą ([Learn](https://learn.microsoft.com/en-us/microsoft-copilot-studio/add-agent-child-agent)). W pierwszym teście nie są potrzebni. Mają sens, gdy dojdzie drugi strumień:

- **„Dowody”:** tylko narzędzie `AD_Dowody` i wąskie instrukcje o rozróżnianiu cytatów i artefaktów.
- **„Brief biznesu”:** klasyfikacja briefu właściciela produktu (wspierany / brak dowodów / sprzeczny) względem plików przebiegu. Tego strumienia nie ma jeszcze w kodzie potoku, więc to osobny krok rozwoju.

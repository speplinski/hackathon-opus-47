# F0. Przygotowanie środowiska (jednorazowo)

Kolejność ma znaczenie: flow odwołuje się do plików, skryptów i promptów po nazwie.

## 1. OneDrive

1. Wgraj folder `AuditableDesign/` z paczki (budujesz go skryptem `narzedzia/przygotuj_onedrive.py`) do katalogu głównego swojego OneDrive.
2. W `AuditableDesign/` utwórz folder `kernel/`, a w nim w Excelu w przeglądarce pusty skoroszyt `AD_Kernel.xlsx`. Akcja *Run script* wymaga wskazania skoroszytu, nawet jeśli skrypt go nie używa.

Struktura po wgraniu:

```
AuditableDesign/
├─ kernel/AD_Kernel.xlsx           pusty skoroszyt dla akcji Run script
├─ konfiguracja/konfiguracja.json  ustawienia przebiegu
│  └─ przyklady/run_request_*.json wzory plików startowych
├─ start/                          plik run_request.json tutaj uruchamia flow
├─ wejscie/
│  ├─ badanie_A/klastry.json       sześć klastrów ze studium (wejście L4)
│  │  ├─ artefakty/*.png           zrzuty ekranu przypisane klastrom
│  │  └─ referencja/<model>/       wyniki studium do porównania
│  └─ badanie_B/wypowiedzi.json    korpus 600 recenzji jako wejście L0
└─ przebiegi/                      flow zapisuje tu folder na każdy przebieg
```

## 2. Office Scripts (deterministyczna część potoku)

Skrypty trafiają do folderu `Documents/Office Scripts` w Twoim OneDrive. Akcja *Run script* szuka ich właśnie tam.

Dla każdego pliku z `kernel/dist/` (`AD_Wejscie`, `AD_Audyt`, `AD_Uzgodnienie`, `AD_Petla`, `AD_Brief`, `AD_Dowody`):

1. Otwórz `AD_Kernel.xlsx` w Excelu w przeglądarce → **Automate** → **New script**.
2. Usuń przykładowy kod, wklej całą zawartość pliku `.ts`, zapisz pod nazwą pliku (np. `AD_Audyt`).
3. Nie edytuj skryptów ręcznie. Zmiany wprowadzaj w `kernel/src/` i przebuduj (`bun build.ts`), żeby zachować testy zgodności z Pythonem.

Każdy skrypt ma sygnaturę `main(workbook, operation, payload)` i zwraca tekst JSON `{ok, operation, result, errors}`.

Limity, które trzeba znać ([Microsoft Learn](https://learn.microsoft.com/en-us/office/dev/scripts/testing/platform-limits)):

- 1600 wywołań *Run script* na użytkownika na dobę;
- 3 wywołania na 10 sekund (konektor);
- 120 sekund na wywołanie;
- parametry do 28,6 MB, żądanie konektora do 5 MB.

Dlatego operacje kernela działają na partiach (np. `l1_messages` buduje wiadomości dla wszystkich wypowiedzi jednym wywołaniem). Administrator może wyłączyć Office Scripts albo ich użycie we flow. Jeśli akcja *Run script* jest niedostępna, sprawdź to z nim w pierwszej kolejności.

## 3. Prompty

Dla każdego pliku z `prompty/` (16 promptów):

1. W Copilot Studio: **Tools** → **Add a tool** → **New prompt** (albo z poziomu flow: akcja *Run a prompt* → nowy prompt).
2. Nazwa jak w pliku `.md` (np. `AD_L4_Cooper`).
3. **Instructions:** wklej zawartość `prompty/instrukcje/NN_*.txt`. W miejscu każdego znacznika `{{nazwa}}` wstaw zmienną wejściową o tej nazwie (w edytorze wpisz `/`). Typ zmiennej podaje tabela „Wejścia” w pliku `.md`.
4. **Model:** zgodnie z planem testu (README, sekcja „Modele”). Modele Claude wymagają włączenia modeli zewnętrznych przez administratora i Microsoft opisuje je jako eksperymentalne ([Learn](https://learn.microsoft.com/en-us/microsoft-copilot-studio/authoring-select-external-response-model)).
5. **Output:** Text. JSON wyciąga i sprawdza kernel.

Instrukcje soczewek mają 24–37 tys. znaków. Dokumentacja nie podaje limitu długości instrukcji, ostrzega jedynie przed opóźnieniami przy bardzo długich ([Learn](https://learn.microsoft.com/en-us/microsoft-copilot-studio/nlu-prompt-node)). Jeśli edytor odrzuci tekst, zgłoś to: trzeba będzie skrócić skill, a to zmienia metodę.

## 4. Połączenia we flow

Flow potrzebuje trzech połączeń:

- OneDrive for Business: odczyt i zapis plików, wyzwalacz.
- Excel Online (Business): *Run script*.
- AI Builder / prompty: *Run a prompt*.

Wszystkie łączysz swoim kontem przy pierwszym dodaniu akcji.

## 5. Kontrola przed pierwszym przebiegiem

Zrób jedną ręczną próbę: flow z akcją *Run script* `AD_Audyt`, operacja `l4_lenses`, payload `{}`. Wynik powinien zawierać sześć soczewek. To potwierdza, że skrypty, skoroszyt i połączenie działają.

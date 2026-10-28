# 14 AD_L8_Ocena

**Warstwa:** L8  
**Model w badaniu:** `claude-opus-4-7`  
**Limit odpowiedzi w badaniu:** 4096 tokenów  
**Skrypt kernela:** `AD_Petla` (wejście: `l8_baseline / l8_tweak_parse`, wyjście: `l8_proposed / l8_loop_step`)

## Ustawienia w Copilot Studio

1. Tools → Add a tool → New prompt. Nazwa: `AD_L8_Ocena`.
2. Instructions: wklej całą zawartość `instrukcje/14_AD_L8_Ocena.txt`.
3. Dla każdego znacznika `{{nazwa}}` usuń go i wstaw w tym miejscu zmienną wejściową o tej samej nazwie (w edytorze wpisz `/`).
4. Model: zgodnie z planem testu (README, sekcja Modele). Temperatura: najniższa dostępna, jeśli ustawienie jest dostępne.
5. Output: Text. JSON wyciąga i sprawdza kernel, więc prompt nie musi mieć zdefiniowanego schematu.

## Wejścia

| Zmienna | Typ | Zawartość |
|---|---|---|
| `wiadomosc` | Text | `reaudit_message` z `l8_baseline` (iteracja 1) albo z `l8_tweak_parse` (iteracje 2+) |

## Uwagi

Ocena projektu po zmianie względem tej samej listy heurystyk. Bramkę akceptacji (Pareto, Tchebycheff) liczy kernel, nie model.

Długość instrukcji: 10 622 znaków.

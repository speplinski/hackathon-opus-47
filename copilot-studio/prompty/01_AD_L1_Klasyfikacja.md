# 01 AD_L1_Klasyfikacja

**Warstwa:** L1  
**Model w badaniu:** `claude-opus-4-6`  
**Limit odpowiedzi w badaniu:** 256 tokenów  
**Skrypt kernela:** `AD_Wejscie` (wejście: `l1_messages`, wyjście: `l1_parse`)

## Ustawienia w Copilot Studio

1. Tools → Add a tool → New prompt. Nazwa: `AD_L1_Klasyfikacja`.
2. Instructions: wklej całą zawartość `instrukcje/01_AD_L1_Klasyfikacja.txt`.
3. Dla każdego znacznika `{{nazwa}}` usuń go i wstaw w tym miejscu zmienną wejściową o tej samej nazwie (w edytorze wpisz `/`).
4. Model: zgodnie z planem testu (README, sekcja Modele). Temperatura: najniższa dostępna, jeśli ustawienie jest dostępne.
5. Output: Text. JSON wyciąga i sprawdza kernel, więc prompt nie musi mieć zdefiniowanego schematu.

## Wejścia

| Zmienna | Typ | Zawartość |
|---|---|---|
| `wiadomosc` | Text | wynik `l1_messages` dla jednej wypowiedzi |

## Uwagi

Prompt z badania mówi o recenzjach Duolingo. Do testu na tym samym korpusie zostaw go bez zmian; dla innych źródeł podmień pierwsze zdanie i słownik tagów (zob. README).

Długość instrukcji: 2 108 znaków.

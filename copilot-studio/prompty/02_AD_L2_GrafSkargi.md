# 02 AD_L2_GrafSkargi

**Warstwa:** L2  
**Model w badaniu:** `claude-opus-4-7`  
**Limit odpowiedzi w badaniu:** 1024 tokenów  
**Skrypt kernela:** `AD_Wejscie` (wejście: `l2_messages`, wyjście: `l2_extract`)

## Ustawienia w Copilot Studio

1. Tools → Add a tool → New prompt. Nazwa: `AD_L2_GrafSkargi`.
2. Instructions: wklej całą zawartość `instrukcje/02_AD_L2_GrafSkargi.txt`.
3. Dla każdego znacznika `{{nazwa}}` usuń go i wstaw w tym miejscu zmienną wejściową o tej samej nazwie (w edytorze wpisz `/`).
4. Model: zgodnie z planem testu (README, sekcja Modele). Temperatura: najniższa dostępna, jeśli ustawienie jest dostępne.
5. Output: Text. JSON wyciąga i sprawdza kernel, więc prompt nie musi mieć zdefiniowanego schematu.

## Wejścia

| Zmienna | Typ | Zawartość |
|---|---|---|
| `wiadomosc` | Text | wynik `l2_messages` dla jednej wypowiedzi |

## Uwagi

Walidację dosłowności cytatów i kwarantannę robi kernel (`l2_extract`), nie model.

Długość instrukcji: 6 898 znaków.

# 04 AD_L3b_Etykieta

**Warstwa:** L3b  
**Model w badaniu:** `claude-haiku-4-5-20251001`  
**Limit odpowiedzi w badaniu:** 512 tokenów  
**Skrypt kernela:** `AD_Wejscie` (wejście: `l3b_message`, wyjście: `l3b_label`)

## Ustawienia w Copilot Studio

1. Tools → Add a tool → New prompt. Nazwa: `AD_L3b_Etykieta`.
2. Instructions: wklej całą zawartość `instrukcje/04_AD_L3b_Etykieta.txt`.
3. Dla każdego znacznika `{{nazwa}}` usuń go i wstaw w tym miejscu zmienną wejściową o tej samej nazwie (w edytorze wpisz `/`).
4. Model: zgodnie z planem testu (README, sekcja Modele). Temperatura: najniższa dostępna, jeśli ustawienie jest dostępne.
5. Output: Text. JSON wyciąga i sprawdza kernel, więc prompt nie musi mieć zdefiniowanego schematu.

## Wejścia

| Zmienna | Typ | Zawartość |
|---|---|---|
| `wiadomosc` | Text | wynik `l3b_message` dla jednego klastra |

Długość instrukcji: 5 876 znaków.

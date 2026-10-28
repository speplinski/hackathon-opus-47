# 03 AD_L3_Grupowanie

**Warstwa:** L3  
**Model w badaniu:** `— (w badaniu: MiniLM + HDBSCAN, bez modelu językowego)`  
**Limit odpowiedzi w badaniu:** 16384 tokenów  
**Skrypt kernela:** `AD_Wejscie` (wejście: `l3_message`, wyjście: `l3_clusters`)

## Ustawienia w Copilot Studio

1. Tools → Add a tool → New prompt. Nazwa: `AD_L3_Grupowanie`.
2. Instructions: wklej całą zawartość `instrukcje/03_AD_L3_Grupowanie.txt`.
3. Dla każdego znacznika `{{nazwa}}` usuń go i wstaw w tym miejscu zmienną wejściową o tej samej nazwie (w edytorze wpisz `/`).
4. Model: zgodnie z planem testu (README, sekcja Modele). Temperatura: najniższa dostępna, jeśli ustawienie jest dostępne.
5. Output: Text. JSON wyciąga i sprawdza kernel, więc prompt nie musi mieć zdefiniowanego schematu.

## Wejścia

| Zmienna | Typ | Zawartość |
|---|---|---|
| `wiadomosc` | Text | wynik `l3_message` (wszystkie węzły triggered_element i lost_value) |

## Uwagi

NOWY KROK, odstępstwo od metody: w badaniu klastrowanie robił enkoder zdań i HDBSCAN. Kernel sprawdza każdy idx i zapisuje recenzję przy każdym cytacie.

Długość instrukcji: 1 508 znaków.

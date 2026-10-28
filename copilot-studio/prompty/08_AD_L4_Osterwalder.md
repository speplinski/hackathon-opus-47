# 08 AD_L4_Osterwalder

**Warstwa:** L4  
**Model w badaniu:** `claude-sonnet-4-6`  
**Limit odpowiedzi w badaniu:** 6144 tokenów  
**Skrypt kernela:** `AD_Audyt` (wejście: `l4_message`, wyjście: `l4_verdict`)

## Ustawienia w Copilot Studio

1. Tools → Add a tool → New prompt. Nazwa: `AD_L4_Osterwalder`.
2. Instructions: wklej całą zawartość `instrukcje/08_AD_L4_Osterwalder.txt`.
3. Dla każdego znacznika `{{nazwa}}` usuń go i wstaw w tym miejscu zmienną wejściową o tej samej nazwie (w edytorze wpisz `/`).
4. Model: zgodnie z planem testu (README, sekcja Modele). Temperatura: najniższa dostępna, jeśli ustawienie jest dostępne.
5. Output: Text. JSON wyciąga i sprawdza kernel, więc prompt nie musi mieć zdefiniowanego schematu.

## Wejścia

| Zmienna | Typ | Zawartość |
|---|---|---|
| `wiadomosc` | Text | wynik `l4_message` dla soczewki `audit-business-alignment` i jednego klastra |
| `zrzut` | Image or document | zrzut ekranu lub eksport makiety z `screenshot_ref` klastra (PNG/JPG/PDF) |

## Uwagi

W siatce badania (wariant multimodalny) każda soczewka dostawała zrzut ekranu przed tekstem wiadomości.

Długość instrukcji: 25 442 znaków.

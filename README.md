# Cópia histórica do site do COEE

Este repositório recupera e organiza uma cópia navegável do antigo site do
Colegiado de Engenharia Elétrica da Universidade Federal de Ouro Preto (UFOP).
O material é histórico e não substitui as informações oficiais atuais. A data
de captura preferencial é **27/07/2024** (`20240727095454`).

## Abrir no VS Code

Clone o repositório e abra a pasta no VS Code:

```powershell
git clone https://github.com/ThainanST/siteCoee.git
code .\siteCoee
```

No terminal integrado, confirme que a pasta atual é a raiz do repositório e
crie o ambiente virtual:

```powershell
py -3.13 -m venv .venv
```

No VS Code, use `Ctrl+Shift+P`, escolha **Python: Select Interpreter** e
selecione `.venv\Scripts\python.exe`. As instruções abaixo chamam esse
executável diretamente, sem depender da ativação do ambiente:

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe .\scripts\recuperar_coee.py
```

O recuperador usa somente a biblioteca padrão do Python. Ele começa pela
página inicial arquivada, segue links internos e recursos do domínio do COEE,
e tenta recuperar documentos da UFOP vinculados às páginas. A API CDX não é
necessária. Cada item é salvo e validado individualmente; uma falha não impede
que os itens restantes sejam tentados.

Para evitar uma execução muito longa, cada chamada tenta no máximo **20 novos
downloads** e informa o progresso no terminal. Itens restantes aparecem como
`pendente` no manifesto. Repita o comando para continuar; use
`--max-downloads 5`, por exemplo, para alterar o limite. As respostas do
Wayback podem ser lentas ou falhar temporariamente. Para interromper, pressione
`Ctrl+C`: o recuperador salva o manifesto e gera o site com o cache disponível.

## Manifesto e retomada

O arquivo `arquivo-coee\manifesto.json` registra URL original, página de
origem, captura pedida e efetiva, caminho da resposta bruta, caminho da cópia
estática, estado e erro. Conteúdo bruto fica em `arquivo-coee\raw\`. O
manifesto diferencia conteúdo recuperado, captura indisponível, falha
temporária, falha de validação e links que precisam de conferência externa.
`origem_invalida` indica links coletados de uma resposta que depois se revelou
uma página de erro do Wayback; não devem ser considerados conteúdo recuperado.

Execute novamente o comando de recuperação para retomar: arquivos brutos
válidos são reutilizados e os demais itens são tentados outra vez. Se a rede
estiver indisponível, é possível reconstruir `docs\` apenas do cache:

```powershell
.\.venv\Scripts\python.exe .\scripts\recuperar_coee.py --offline
```

Um inventário opcional do `wayback-dl` pode ser colocado na raiz como
`COEE-inventario-parcial.json` ou em `arquivo-coee\COEE-inventario-parcial.json`.
O cache e o manifesto são locais e ignorados pelo Git; `docs\` pode ser
versionada. Num clone sem cache, `--offline` não baixa nem reconstrói o conteúdo
histórico já publicado em `docs\`.

## Prévia local

Para gerar o site e iniciá-lo em `http://localhost:8000/siteCoEe/`, execute:

```powershell
.\.venv\Scripts\python.exe .\scripts\recuperar_coee.py --offline --serve-preview
```

Use `--port 8080` para escolher outra porta. A prévia publica somente os
arquivos de `docs\` e mapeia esse conteúdo em `/siteCoEe/`. A página inicial é
`docs\index.html`. Links não recuperados apontam para a captura do Wayback e
são identificados como pendentes; confira a situação e o erro correspondente
no manifesto.

## Publicação futura no GitHub Pages

Após revisar e versionar o conteúdo de `docs\`, abra as configurações do
repositório no GitHub e vá a **Settings → Pages → Build and deployment**.
Selecione **Deploy from a branch**, escolha a branch que contém o site e a
pasta **/docs**, e salve. Este projeto não publica nem altera configurações
remotamente.

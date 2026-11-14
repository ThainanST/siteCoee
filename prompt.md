# Objetivo

Recupere o conteúdo do antigo site do Colegiado de Engenharia Elétrica da UFOP (COEE) e gere uma cópia histórica navegável no repositório `siteCoee`. A cópia deve funcionar localmente e ficar pronta para publicação futura no GitHub Pages pela pasta `docs/`.

Trabalhe diretamente no workspace do VS Code. Inspecione os arquivos existentes e o estado do Git, preserve minhas alterações e reaproveite scripts, inventários e capturas que já estiverem disponíveis. Implemente e teste o fluxo. Deixe commit, push e publicação para eu decidir depois.

# Fonte e contexto

- Site original: https://coee.ufop.br/
- Captura de referência: https://web.archive.org/web/20240727095454/https://coee.ufop.br/
- Data de referência: 27/07/2024, timestamp `20240727095454`.
- Ambiente: Windows, VS Code, terminal PowerShell e Python 3.13.15 disponível pelo comando `py`.
- O `wayback-dl` consultou a API CDX, mas a paginação falhou repetidamente com HTTP 503. Com `--max-pages 1`, consegui gerar `COEE-inventario-parcial.json`, com 14 entradas.
- Esse inventário inclui a página inicial, Docentes, Ementas, Contato, Bolsas, Estágio Curricular, Aproveitamento de Estudos, Concessão de Ênfase, ATV100 e Atendimento Psicológico, além de rotas técnicas do Drupal. TCC, Sobre o Colegiado e PDFs ficaram de fora do inventário parcial. Procure esses itens pelos links das páginas arquivadas.

# 1. Recuperação e rastreabilidade

Crie ou revise `scripts/recuperar_coee.py`.

- Comece pela página inicial da captura de referência. Descubra as URLs reais pelo menu e pelos links do HTML. Percorra as páginas internas e seus anexos. Use `COEE-inventario-parcial.json` como complemento opcional para obter URLs e timestamps conhecidos. O fluxo principal deve funcionar mesmo quando a API CDX estiver indisponível.
- Priorize TCC, Sobre o Colegiado, estágio, ementas, resoluções e formulários. Inclua as demais seções acessíveis e os recursos usados pelas páginas, como imagens e CSS. Ignore rotas administrativas do Drupal, login e recuperação de senha.
- Recupere documentos vinculados em outros domínios da UFOP quando houver captura. Registre links externos relevantes que precisem de conferência manual, inclusive documentos sem extensão na URL. Limite a navegação recursiva ao domínio do COEE.
- Resolva links relativos usando a URL original da página e trate links que já venham com o prefixo do Wayback. Preserve âncoras e trate caminhos com acentos e URLs com parâmetros quando eles identificarem conteúdos distintos.
- Use a data de referência como preferência. Ao obter uma captura de outra data, registre a data efetivamente retornada. Diferencie falha temporária, captura indisponível e arquivo válido recuperado.
- Salve as respostas brutas em `arquivo-coee/`. Valide o tipo de conteúdo antes de considerar um download concluído, inclusive a assinatura `%PDF-` para PDFs. Use timeout, poucas tentativas com espera progressiva e reaproveitamento de arquivos válidos em novas execuções. Uma falha individual deve deixar um registro e permitir que a recuperação dos outros itens continue.
- Gere um manifesto com URL original, página onde o link foi encontrado, captura solicitada, captura efetiva, arquivo local, status e erro, quando houver. O manifesto deve permitir identificar o que falta e retomar o trabalho.

# 2. Cópia estática para GitHub Pages

Organize o projeto assim:

    README.md
    .gitignore
    scripts/recuperar_coee.py
    arquivo-coee/              capturas brutas, cache e manifesto
    docs/
        index.html
        .nojekyll
        ...                    páginas, anexos e recursos da cópia navegável

- Gere o site estático em `docs/`, com `index.html` na entrada e `.nojekyll` vazio. Python serve para recuperar e gerar os arquivos. A versão publicada deve funcionar só com arquivos estáticos.
- Preserve a estrutura de navegação e o conteúdo histórico disponível. Identifique a versão como cópia histórica do site do COEE para que visitantes saibam a origem do material.
- Reescreva os links de páginas, anexos, imagens e CSS recuperados para caminhos relativos. Evite caminhos começando por `/` e links internos apontando para `coee.ufop.br`, pois o site pode ser servido sob `/siteCoee/`.
- Para itens sem cópia local, deixe um link identificado para consulta no Wayback e registre a pendência. Indique quando a URL do Wayback ainda precisa ser verificada.
- Inclua `.venv/`, `__pycache__/` e `arquivo-coee/` no `.gitignore`. Mantenha `docs/` disponível para versionamento. A geração deve preservar arquivos existentes fora das saídas que o script controla.

# 3. README para um clone novo no VS Code

Escreva o README em português e use blocos PowerShell copiáveis. Explique como clonar ou abrir `siteCoee` no VS Code, abrir o terminal integrado na raiz do repositório, criar a `.venv` e selecioná-la em `Python: Select Interpreter`.

Inclua estes comandos:

    py -3.13 -m venv .venv
    .\.venv\Scripts\python.exe --version
    .\.venv\Scripts\python.exe .\scripts\recuperar_coee.py

Use o executável da `.venv` diretamente nas instruções. Prefira a biblioteca padrão. Se uma dependência externa for necessária, declare-a em `requirements.txt` e documente a instalação usando `.\.venv\Scripts\python.exe -m pip`.

Explique onde encontrar e como ler o manifesto, como retomar a recuperação, como abrir a prévia local e como publicar posteriormente pelo GitHub em Settings → Pages → Deploy from a branch, selecionando a branch que contém o site e a pasta `/docs`.

# 4. Testes e entrega

- Rode o script a partir da captura de referência. Se o Wayback falhar, use as capturas locais disponíveis para testar a extração de links e a geração estática. Identifique quais verificações ainda dependem da rede.
- Teste a prévia em `http://localhost:<porta>/siteCoee/`, expondo o conteúdo de `docs/` nesse subcaminho para simular o GitHub Pages. Confira a página inicial, a navegação entre páginas, o carregamento de CSS e imagens e a abertura de PDFs quando recuperados.
- Verifique se os links locais em `docs/` apontam para arquivos existentes. Dê atenção especial a TCC, Sobre o Colegiado e aos anexos dessas seções.
- Ao terminar, informe quais arquivos foram criados ou alterados, o comando para abrir a prévia e o que foi efetivamente recuperado. Liste as lacunas com o motivo registrado no manifesto. Só conte como recuperado o material cujo arquivo foi salvo e validado.
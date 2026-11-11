Analiza el siguiente mensaje de un grupo de Telegram. Si incluye el contenido de la página enlazada de la oferta, úsalo como parte de la misma oferta: suele tener la descripción completa, los requisitos, el salario y cómo aplicar (si contradice al mensaje, prefiere la página; y dentro de la página, la descripción escrita manda sobre los metadatos).

1. is_job_offer: true solo si es una oferta de empleo o proyecto remunerado para un perfil técnico de software (desarrollo, QA, DevOps, datos, diseño de producto digital…). Es false si es un curso, publicidad, alguien que busca trabajo, una pregunta, una noticia o spam. confidence: tu seguridad de 0 a 1.
2. Si es una oferta, extrae los campos. Si contiene varios puestos, describe el más técnico (desarrollo de software) y menciona los demás en summary.
   - seniority: según lo que pida la oferta (años de experiencia o nivel). null si no lo indica.
   - modality: "remote" si todo el trabajo es a distancia; "hybrid" si combina; "onsite" si es presencial o exige reubicarse a una oficina, aunque haya un periodo inicial remoto. Si deja elegir entre remoto y presencial (p. ej. "Remoto | Presencial", "remoto o presencial", "remote or on-site") sin exigir ir a la oficina, usa "remote" y menciona la opción en summary. null si no lo indica.
   - salary_*: cifras tal como aparecen (salary_min = salary_max si es una sola cifra). salary_currency con código ISO (USD, EUR, MXN…; "MLC" si lo dice). salary_period según indique (hour, month, year).
   - contract_type: full_time, part_time, freelance (proyecto o por horas), internship u other. null si no lo indica.
   - offer_language: idioma en que está escrito el mensaje ("es", "en"…).
   - contacts: emails, usuarios de Telegram (sin @) y enlaces que aparecen literalmente en el mensaje, en la lista de enlaces o en el contenido de la página enlazada.
   - preferred_channel_stated: el método que la oferta pide explícitamente para aplicar: "email" (enviar CV a un correo), "telegram_dm" (escribir a un @usuario o "por privado"), "form" (formulario o enlace) o "none" si no pide ninguno.
   - application_instructions: copia literalmente la frase que explica cómo aplicar (asunto, referencia, datos a enviar), o null.
   - summary: 1-2 frases en español con lo esencial (puesto, empresa, requisitos clave, condiciones llamativas).

<${tag}>
${offer_text}
</${tag}>

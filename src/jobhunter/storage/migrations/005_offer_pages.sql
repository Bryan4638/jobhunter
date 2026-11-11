-- Página enlazada de la oferta («Ir a la oferta»): se descarga una vez y se guarda su texto
-- para la extracción, la reevaluación y la redacción (docs/05 «Enlaces»).
ALTER TABLE raw_messages ADD COLUMN page_url TEXT;
ALTER TABLE raw_messages ADD COLUMN page_text TEXT;

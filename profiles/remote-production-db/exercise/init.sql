CREATE TABLE scaffold_tunnel_probe (
    id integer PRIMARY KEY,
    value text NOT NULL
);
INSERT INTO scaffold_tunnel_probe (id, value)
VALUES (1, 'remote-production-data');

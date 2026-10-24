package PLACEHOLDER;
// El workflow reescribe esta línea al paquete real del proyecto en el momento de ejecutarse.

import static org.assertj.core.api.Assertions.assertThat;

import java.util.HashMap;
import java.util.Map;
import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Order;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestMethodOrder;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.SpringBootTest.WebEnvironment;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.RestTemplate;

/**
 * Prueba de verificación automatizada para el Codelab Spring Boot 05.
 */
@SpringBootTest(webEnvironment = WebEnvironment.RANDOM_PORT)
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class ClienteVerificationTest {

    @LocalServerPort
    private int port;

    private final RestTemplate restTemplate = new RestTemplate();

    private static Long idCreado;

    private String url(String path) {
        return "http://localhost:" + port + path;
    }

    @Test
    @Order(1)
    void crearCliente_valido_debeRetornar201ConId() {
        Map<String, Object> body = new HashMap<>();
        body.put("nombre", "Ana Pérez");
        body.put("email", "ana.perez@example.com");

        ResponseEntity<Map> response = restTemplate.postForEntity(url("/clientes"), body, Map.class);

        assertThat(response.getStatusCode())
                .as("POST /clientes con datos válidos debe retornar 201 Created")
                .isEqualTo(HttpStatus.CREATED);

        Object id = response.getBody() != null ? response.getBody().get("id") : null;
        assertThat(id).as("La respuesta debe incluir el id generado del cliente creado").isNotNull();
        idCreado = Long.valueOf(id.toString());
    }

    @Test
    @Order(2)
    void buscarCliente_idExistente_debeRetornar200ConDatos() {
        assertThat(idCreado)
                .as("El test de creación (Order 1) debe haber guardado un id antes de llegar aquí")
                .isNotNull();

        ResponseEntity<Map> response = restTemplate.getForEntity(url("/clientes/" + idCreado), Map.class);

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(response.getBody()).isNotNull();
        assertThat(response.getBody().get("nombre")).isEqualTo("Ana Pérez");
    }

    @Test
    @Order(3)
    void buscarCliente_idInexistente_debeRetornar404() {
        try {
            restTemplate.getForEntity(url("/clientes/999999"), Map.class);
            org.assertj.core.api.Assertions.fail(
                    "Se esperaba un 404 Not Found al buscar un id inexistente, pero no se lanzó ninguna excepción");
        } catch (HttpClientErrorException ex) {
            assertThat(ex.getStatusCode())
                    .as("Buscar un id que no existe debe retornar 404")
                    .isEqualTo(HttpStatus.NOT_FOUND);
        }
    }
}

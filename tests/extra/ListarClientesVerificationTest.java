package PLACEHOLDER;
// El workflow reescribe esta línea al paquete real del proyecto en el momento de ejecutarse.

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.SpringBootTest.WebEnvironment;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.RestTemplate;

/**
 * Reto adicional: listar todos los clientes.
 */
@SpringBootTest(webEnvironment = WebEnvironment.RANDOM_PORT)
class ListarClientesVerificationTest {

    @LocalServerPort
    private int port;

    private final RestTemplate restTemplate = new RestTemplate();

    @Test
    void listarClientes_debeRetornar200ConUnaListaJson() {
        String url = "http://localhost:" + port + "/clientes";

        ResponseEntity<List> response = restTemplate.getForEntity(url, List.class);

        assertThat(response.getStatusCode())
                .as("GET /clientes debe retornar 200 OK con la lista de clientes")
                .isEqualTo(HttpStatus.OK);
        assertThat(response.getBody()).isInstanceOf(List.class);
    }
}

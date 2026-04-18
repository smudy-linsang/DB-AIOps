"""
Local UI demo script (not a Django test).
"""


def run_demo() -> None:
    import pygame

    pygame.init()
    screen = pygame.display.set_mode((800, 600))
    pygame.display.set_caption("ACME Corporate Reader - Demo")
    font = pygame.font.SysFont("Arial", 24)
    clock = pygame.time.Clock()
    running = True

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
        screen.fill((30, 30, 50))
        text = font.render("ACME demo (ESC/close to exit)", True, (220, 220, 220))
        screen.blit(text, (220, 280))
        pygame.display.flip()
        clock.tick(30)

    pygame.quit()


if __name__ == "__main__":
    run_demo()

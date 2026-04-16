import pygame
import os

pygame.init()

WIDTH, HEIGHT = 800, 600
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("ACME Corporate Reader - Test Display")

# Colors
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
DARK_BLUE = (30, 30, 50)
LIGHT_BLUE = (0, 150, 255)
GREEN = (0, 200, 100)
ORANGE = (255, 165, 0)
GRAY = (100, 100, 100)
LIGHT_GRAY = (200, 200, 200)

# Fonts
font_large = pygame.font.SysFont("Arial", 36, bold=True)
font_medium = pygame.font.SysFont("Arial", 24)
font_small = pygame.font.SysFont("Arial", 18)

# Mode
current_mode = 0  # 0 = fingerprint registration, 1 = face recognition
modes = ["FINGERPRINT_REG", "FACE_RECOGNITION"]

def draw_fingerprint_mode():
    # Title
    title = font_large.render("Register with Fingerprint", True, WHITE)
    screen.blit(title, (WIDTH//2 - title.get_width()//2, 50))
    
    # Subtitle
    subtitle = font_medium.render("Place your finger on the reader", True, LIGHT_GRAY)
    screen.blit(subtitle, (WIDTH//2 - subtitle.get_width()//2, 120))
    
    # Fingerprint icon (circle)
    center = (WIDTH//2, HEIGHT//2)
    pygame.draw.circle(screen, LIGHT_BLUE, center, 120, 3)
    pygame.draw.circle(screen, LIGHT_BLUE, center, 100, 2)
    pygame.draw.circle(screen, LIGHT_BLUE, center, 80, 2)
    pygame.draw.circle(screen, LIGHT_BLUE, center, 60, 2)
    pygame.draw.circle(screen, LIGHT_BLUE, center, 40, 2)
    pygame.draw.circle(screen, LIGHT_BLUE, center, 20, 1)
    
    # Status
    status = font_small.render("Status: Waiting for fingerprint...", True, ORANGE)
    screen.blit(status, (WIDTH//2 - status.get_width()//2, HEIGHT - 150))
    
    # Employee ID prompt
    eid = font_small.render("Employee ID: [Scan card first]", True, LIGHT_GRAY)
    screen.blit(eid, (WIDTH//2 - eid.get_width()//2, HEIGHT - 100))

def draw_face_mode():
    # Title
    title = font_large.render("Face Recognition", True, WHITE)
    screen.blit(title, (WIDTH//2 - title.get_width()//2, 50))
    
    # Subtitle
    subtitle = font_medium.render("Position face in the frame", True, LIGHT_GRAY)
    screen.blit(subtitle, (WIDTH//2 - subtitle.get_width()//2, 120))
    
    # Face frame (rectangle)
    frame_rect = pygame.Rect(WIDTH//2 - 120, HEIGHT//2 - 160, 240, 320)
    pygame.draw.rect(screen, GREEN, frame_rect, 3)
    
    # Corner markers
    corner_len = 30
    corners = [
        (frame_rect.topleft, (1, 1)),
        (frame_rect.topright, (-1, 1)),
        (frame_rect.bottomleft, (1, -1)),
        (frame_rect.bottomright, (-1, -1))
    ]
    for pos, (dx, dy) in corners:
        pygame.draw.line(screen, GREEN, pos, (pos[0] + corner_len * dx, pos[1]), 4)
        pygame.draw.line(screen, GREEN, pos, (pos[0], pos[1] + corner_len * dy), 4)
    
    # Status
    status = font_small.render("Status: Scanning face...", True, ORANGE)
    screen.blit(status, (WIDTH//2 - status.get_width()//2, HEIGHT - 150))
    
    # Instructions
    inst = font_small.render("Look directly at camera", True, LIGHT_GRAY)
    screen.blit(inst, (WIDTH//2 - inst.get_width()//2, HEIGHT - 100))

running = True
clock = pygame.time.Clock()

while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_SPACE:
                current_mode = (current_mode + 1) % 2
            elif event.key == pygame.K_ESCAPE:
                running = False
    
    screen.fill(DARK_BLUE)
    
    if current_mode == 0:
        draw_fingerprint_mode()
    else:
        draw_face_mode()
    
    # Mode indicator at bottom
    mode_text = font_small.render(f"Mode: {modes[current_mode]}  |  Press SPACE to switch", True, GRAY)
    screen.blit(mode_text, (WIDTH//2 - mode_text.get_width()//2, HEIGHT - 40))
    
    pygame.display.flip()
    clock.tick(30)

pygame.quit()

// -*- mode: c++; c-basic-offset: 2; indent-tabs-mode: nil; -*-
#include "led-matrix.h"
#include "graphics.h"

#include <getopt.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <vector>
#include <string>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <errno.h>
#include <thread>
#include <sstream>

using namespace rgb_matrix;

volatile bool interrupt_received = false;
static void InterruptHandler(int signo) {
    interrupt_received = true;
}

struct TextLine {
    std::string text;
    int x;
    int y;
    Color color;
};

// Global variables for shared state
RGBMatrix* canvas = nullptr;
rgb_matrix::Font font;
std::vector<TextLine> lines;
Color bg_color(0, 0, 0);
const char* FIFO_PATH = "/tmp/led_matrix_fifo";

static int usage(const char *progname) {
    fprintf(stderr, "usage: %s [options]\n", progname);
    fprintf(stderr, "Options:\n"
            "\t-f <font-file>    : Use given font.\n"
            "\t-x <x-origin>     : X-Origin of displaying text (Default: 0)\n"
            "\t-y <y-origin>     : Y-Origin of displaying text (Default: 0)\n"
            "\t-B <r,g,b>        : Background-color (Default: 0,0,0)\n"
            "\t-L <layout>       : Layout. 0=plain, 1=snake (Default: 0)\n"
            "\nDisplay Options:\n");
    rgb_matrix::PrintMatrixFlags(stderr);
    return 1;
}

static bool parseColor(Color *c, const char *str) {
    return sscanf(str, "%hhu,%hhu,%hhu", &c->r, &c->g, &c->b) == 3;
}

void updateDisplay(const std::vector<TextLine>& lines) {
    if (!canvas) return;
    
    canvas->Fill(bg_color.r, bg_color.g, bg_color.b);
    for (const auto &line : lines) {
        rgb_matrix::DrawText(canvas, font, line.x, line.y, 
                           line.color, nullptr, line.text.c_str());
    }
}

// Parse a message in the format "text1|text2|r1,g1,b1|r2,g2,b2"
bool parseMessage(const std::string& message, std::vector<TextLine>& lines) {
    std::stringstream ss(message);
    std::string item;
    std::vector<std::string> items;
    
    // Split message by '|' delimiter
    while (std::getline(ss, item, '|')) {
        items.push_back(item);
    }
    
    // We need 4 items: text1, text2, color1, color2
    if (items.size() != 4) {
        return false;
    }
    
    // Parse colors
    Color color1, color2;
    if (!parseColor(&color1, items[2].c_str())) {
        return false;
    }
    if (!parseColor(&color2, items[3].c_str())) {
        return false;
    }
    
    // Update lines with new text and colors
    lines[0].text = items[0];
    lines[0].color = color1;
    lines[1].text = items[1];
    lines[1].color = color2;
    
    return true;
}

void readFromPipe() {
    int fd;
    char buffer[1024];
    
    while (!interrupt_received) {
        fd = open(FIFO_PATH, O_RDONLY);
        if (fd == -1) {
            fprintf(stderr, "Error opening FIFO: %s\n", strerror(errno));
            sleep(1);
            continue;
        }

        ssize_t bytes_read = read(fd, buffer, sizeof(buffer) - 1);
        close(fd);

        if (bytes_read > 0) {
            buffer[bytes_read] = '\0';
            if (parseMessage(buffer, lines)) {
                updateDisplay(lines);
            } else {
                fprintf(stderr, "Error parsing message format\n");
            }
        }
    }
}

int main(int argc, char *argv[]) {
    RGBMatrix::Options matrix_options;
    rgb_matrix::RuntimeOptions runtime_opt;

    signal(SIGTERM, InterruptHandler);
    signal(SIGINT, InterruptHandler);

    if (!rgb_matrix::ParseOptionsFromFlags(&argc, &argv,
                                         &matrix_options, &runtime_opt)) {
        return usage(argv[0]);
    }

    const char *bdf_font_file = NULL;
    int x_orig = 0;
    int y_orig = 0;

    int opt;
    while ((opt = getopt(argc, argv, "f:x:y:B:L:")) != -1) {
        switch (opt) {
        case 'f': bdf_font_file = strdup(optarg); break;
        case 'x': x_orig = atoi(optarg); break;
        case 'y': y_orig = atoi(optarg); break;
        case 'B':
            if (!parseColor(&bg_color, optarg)) {
                fprintf(stderr, "Invalid background color spec: %s\n", optarg);
                return usage(argv[0]);
            }
            break;
        case 'L':
            matrix_options.chain_length = atoi(optarg);
            break;
        default:
            return usage(argv[0]);
        }
    }

    if (bdf_font_file == NULL) {
        fprintf(stderr, "Need to specify BDF font-file with -f\n");
        return usage(argv[0]);
    }

    // Create named pipe if it doesn't exist
    if (mkfifo(FIFO_PATH, 0666) == -1 && errno != EEXIST) {
        fprintf(stderr, "Failed to create FIFO: %s\n", strerror(errno));
        return 1;
    }
    
    // Ensure FIFO has correct permissions (readable/writable by all users)
    if (chmod(FIFO_PATH, 0666) == -1) {
        fprintf(stderr, "Failed to set FIFO permissions: %s\n", strerror(errno));
        return 1;
    }

    canvas = RGBMatrix::CreateFromOptions(matrix_options, runtime_opt);
    if (canvas == NULL)
        return 1;

    if (!font.LoadFont(bdf_font_file)) {
        fprintf(stderr, "Couldn't load font '%s'\n", bdf_font_file);
        return 1;
    }

    // Initialize lines with empty text and default white color
    TextLine line1 = {
        "",
        x_orig,
        y_orig + font.baseline(),
        Color(255, 255, 255)
    };
    lines.push_back(line1);

    TextLine line2 = {
        "",
        x_orig,
        y_orig + font.baseline() + font.height() + 2,
        Color(255, 255, 255)
    };
    lines.push_back(line2);

    // Start reading from pipe in a separate thread
    std::thread pipe_thread(readFromPipe);
    
    // Main loop just keeps the program running
    while (!interrupt_received) {
        usleep(100000);  // Sleep for 100ms
    }

    // Cleanup
    pipe_thread.join();
    canvas->Clear();
    delete canvas;
    unlink(FIFO_PATH);  // Remove the FIFO

    return 0;
}

from PIL import Image
import pytesseract

# Replace 'path/to/your/image.png' with the actual path to your local image file
local_filename =  "/Users/admin/PycharmProjects/MasygExtractorFastAPI/tests/data/real_test_image.png"  # Adjust the path as needed.


# Open the file in binary read mode
with open(local_filename, "rb") as file:
    # Read the file content if needed, or pass the file object to PIL
    try:
        # Open the image using PIL
        image = Image.open(file)
        image.verify()  # Verify the image is intact
        print("Image opened and verified successfully.")

        # Re-open the image to extract text (verify() might close the image file)
        file.seek(0)  # Reset the file pointer
        image = Image.open(file)
        text = pytesseract.image_to_string(image)

    except Exception as e:
        print("Error opening or processing the image:", e)

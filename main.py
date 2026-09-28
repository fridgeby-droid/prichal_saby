import os
from dotenv import load_dotenv
load_dotenv()
from app.server import app
if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.getenv('PORT', '8000')), workers=1)

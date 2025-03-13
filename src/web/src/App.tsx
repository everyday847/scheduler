import React, {ChangeEvent, FormEvent, useState} from 'react';
import logo from './logo.svg';
import './App.css';
// import {Button, TextField} from "@mui/material";
import {Button, ChakraProvider, defaultSystem, Input} from "@chakra-ui/react";

function App() {

    // State for form data
    const [formData, setFormData] = useState({
        sr_ncc_fellows: '',
        email: '',
    });

    // State for response data
    const [responseData, setResponseData] = useState(null);

    // Handle form input changes
    const handleInputChange = (e: ChangeEvent) => {
        const { name, value } = e.target as HTMLInputElement;
        setFormData({ ...formData, [name]: value });
    };

    const handleClick = () => {
        const dataToSend = { key: 'value' }; // Replace with the actual data you want to send

        fetch('http://127.0.0.1:5000/api/schedule', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(dataToSend),
        })
            .then(response => response.json())
            .then(data => {
                console.log('Success:', data);
            })
            .catch((error) => {
                console.error('Error:', error);
            });
    };

  return (
    <div className="App">
      <header className="App-header">
        {/*<img src={logo} className="App-logo" alt="logo" />*/}
        <p>
          {/*Edit <code>src/App.tsx</code> and save to reload.*/}
          Click the button to generate a schedule.
        </p>
          {/*send request to flask on click*/}
          <ChakraProvider value={defaultSystem}>
            <Input
              placeholder="Senior NCC Fellows"
              name="sr_ncc_fellows"
              value={formData.sr_ncc_fellows}
              onChange={handleInputChange}
              // fullWidth
            />
            <Button value={''} variant="outline" color="primary" onClick={handleClick}>Generate! </Button>
          </ChakraProvider>
      </header>
    </div>
  );
}

export default App;

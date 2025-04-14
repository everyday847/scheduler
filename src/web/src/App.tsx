import React, {ChangeEvent, FormEvent, useState} from 'react';
import logo from './logo.svg';
import './App.css';
import {Button, TextField} from "@mui/material";
import {DateField} from "@mui/x-date-pickers";
import {TagsInput} from "react-tag-input-component";
// import {Button, ChakraProvider, defaultSystem, Input} from "@chakra-ui/react";

function App() {

    // State for form data
    const [formData, setFormData] = useState({
        sr_ncc_fellows: [] as string[],
        jr_ncc_fellows: [] as string[],
        stroke_fellows: [] as string[],
        CCM_fellows: [] as string[],
        NH_fellows: [] as string[],
        fellow_week_pairs: {} as Map<string, Array<number>>
    });

    // State for response data
    const [responseData, setResponseData] = useState(null);

    // Handle form input changes
    const handleInputChange = (e: ChangeEvent) => {
        const { name, value } = e.target as HTMLInputElement;
        setFormData({ ...formData, [name]: value });
    };

    const setSrNCCFellows = (t: string[]) => {
        setFormData({ ...formData, sr_ncc_fellows: t });
    };
    const setJrNCCFellows = (t: string[]) => {
        setFormData({ ...formData, jr_ncc_fellows: t });
    };
    const setStrokeFellows = (t: string[]) => {
        setFormData({ ...formData, stroke_fellows: t });
    };
    const setCCMFellows = (t: string[]) => {
        setFormData({ ...formData, CCM_fellows: t });
    };
    const setNHFellows = (t: string[]) => {
        setFormData({ ...formData, NH_fellows: t });
    };
    const setVacations = (fellow: string, n: number[]) => {
        // setFormData({ ...formData, fellow_week_pairs: {...formData.fellow_week_pairs, fellow: n} });
        let itt = formData.fellow_week_pairs;
        itt.set(fellow, n);
        setFormData({ ...formData, fellow_week_pairs: itt });
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
            {/*send request to flask on click*/}
            <pre>{JSON.stringify(formData.sr_ncc_fellows)}</pre>
            <TagsInput
                placeHolder="Senior NCC Fellows"
                name="sr_ncc_fellows"
                value={formData.sr_ncc_fellows}
                onChange={setSrNCCFellows}
                // fullWidth
            />
            <pre>{JSON.stringify(formData.jr_ncc_fellows)}</pre>
            <TagsInput
                placeHolder="Junior NCC Fellows"
                name="jr_ncc_fellows"
                value={formData.jr_ncc_fellows}
                onChange={setJrNCCFellows}
                // fullWidth
            />
            <pre>{JSON.stringify(formData.stroke_fellows)}</pre>
            <TagsInput
                placeHolder="Stroke Fellows"
                name="stroke_fellows"
                value={formData.stroke_fellows}
                onChange={setStrokeFellows}
                // fullWidth
            />
            <pre>{JSON.stringify(formData.CCM_fellows)}</pre>
            <TagsInput
                placeHolder="CCM Fellows"
                name="CCM_fellows"
                value={formData.CCM_fellows}
                onChange={setCCMFellows}
                // fullWidth
            />
            <pre>{JSON.stringify(formData.NH_fellows)}</pre>
            <TagsInput
                placeHolder="NH Fellows"
                name="NH_fellows"
                value={formData.NH_fellows}
                onChange={setNHFellows}
                // fullWidth
            />
            <div>
                <p>Vacation</p>
                {
                    formData.sr_ncc_fellows.map((fellow, index) => (
                        <div>
                            <DateField
                                label="Controlled field"
                                value={formData.fellow_week_pairs.get(fellow)}
                                onChange={(newValue) => setVacations(newValue, fellow, 0)}
                            />
                            <DateField
                                label="Controlled field"
                                value={formData.fellow_week_pairs.get(fellow)}
                                onChange={(newValue) => setVacations(newValue, fellow, 1)}
                            />
                            <DateField
                                label="Controlled field"
                                value={formData.fellow_week_pairs.get(fellow)}
                                onChange={(newValue) => setVacations(newValue, fellow, 2)}
                            />
                        </div>
                        )
                    )
                }
            </div>
            <p>
                {/*Edit <code>src/App.tsx</code> and save to reload.*/}
                Click the button to generate a schedule.
            </p>
            <Button value={''} color="primary" onClick={handleClick}>Generate! </Button>
        </header>
    </div>
  );
}

export default App;
